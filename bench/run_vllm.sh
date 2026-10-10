#!/bin/bash
# Serve NVIDIA's NVFP4 checkpoint with vLLM and run the same pipeline against it.
#   bash run_vllm.sh <tag> [extra vllm serve flags...]   (single resident, then two 10-resident batches)
# Flags follow the DGX Spark recipe on the model card / vLLM blog (marlin MoE,
# fp8 KV cache, flashinfer mamba backend, prefix caching, nemotron_v3 reasoning
# parser), with the context capped at AUC's 8192 and few concurrent sequences,
# since AUC sends one request at a time.
set -euo pipefail
B=~/auc-bench
PY=~/.local/share/auc/app/current/backend/venv/bin/python
IMAGE=vllm/vllm-openai:v0.27.1
NAME=auc-bench-vllm
MODEL_DIR=/models/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4
SERVED=nemotron-3.5-lightning-nvfp4
tag=$1; shift 1
out=$B/results/$tag; mkdir -p "$out" $B/cache/vllm
PORT=8090
# The Spark recipe's --moe-backend marlin; set MOE_BACKEND= (empty) to let vLLM choose,
# which the built-in MTP drafter needs (its MoE layers are not quantized).
MOE_BACKEND=${MOE_BACKEND-marlin}
[ -z "$MOE_BACKEND" ] && unset MOE_BACKEND

for m in $(curl -s localhost:11434/api/ps | python3 -c 'import sys,json; print(" ".join(x["name"] for x in json.load(sys.stdin)["models"]))'); do
  curl -s localhost:11434/api/generate -d "{\"model\":\"$m\",\"keep_alive\":0}" > /dev/null
done
docker rm -f $NAME > /dev/null 2>&1 || true
for f in $B/models/hf/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4/*.safetensors; do
  python3 -c "import os,sys; fd=os.open(sys.argv[1], os.O_RDONLY); os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)" "$f"
done

ARGS=(--model $MODEL_DIR --served-model-name $SERVED
      --max-model-len 8192 --max-num-seqs 4 --gpu-memory-utilization 0.45
      ${MOE_BACKEND+--moe-backend $MOE_BACKEND} --kv-cache-dtype fp8 --enable-prefix-caching
      --mamba-backend flashinfer --mamba-cache-mode align
      --reasoning-parser nemotron_v3 --trust-remote-code
      --enable-prompt-tokens-details)
echo "image: $IMAGE  args: ${ARGS[*]} $*" > "$out/server-info.txt"

t0=$(date +%s.%N)
docker run -d --name $NAME --gpus all --ipc=host \
  -p 127.0.0.1:$PORT:8000 \
  -v $B/models/hf:/models:ro -v $B/cache/vllm:/root/.cache \
  $IMAGE "${ARGS[@]}" "$@" > /dev/null
trap 'docker logs $NAME > "$out/server.log" 2>&1 || true; docker rm -f $NAME > /dev/null 2>&1 || true' EXIT
until curl -sf localhost:$PORT/health > /dev/null; do
  [ "$(docker inspect -f '{{.State.Running}}' $NAME 2>/dev/null)" = true ] || { docker logs --tail 40 $NAME; exit 1; }
  sleep 1
done
t1=$(date +%s.%N)
python3 -c "print('startup_s', round($t1-$t0, 2))" | tee "$out/startup.txt"
docker exec $NAME python3 -c "import vllm; print('vllm', vllm.__version__)" >> "$out/server-info.txt" 2>&1 || true

for run in single:synthetic-05 batch:all batch-r2:all; do
  r=${run%%:*}; who=${run#*:}; mkdir -p "$out/$r"
  $PY $B/bench_pipeline.py --backend vllm --base-url http://127.0.0.1:$PORT \
      --model $SERVED --residents $who --out "$out/$r" --tag "$tag-$r" | tee "$out/$r/stdout.txt"
  cp "$out/startup.txt" "$out/$r/" 
done
docker logs $NAME > "$out/server.log" 2>&1 || true
for r in single batch batch-r2; do cp "$out/server.log" "$out/$r/server.log" 2>/dev/null || true; done
python3 $B/analyze.py "$out/single" "$out/batch" "$out/batch-r2"
