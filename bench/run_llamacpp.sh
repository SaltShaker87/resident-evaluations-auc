#!/bin/bash
# Serve the production GGUF with llama-server and run the same pipeline against it.
#   bash run_llamacpp.sh <tag> <upstream|bundled> [extra llama-server flags...]   (single, then two batches)
# upstream = llama.cpp built from source on this machine (native sm_121a kernels)
# bundled  = the llama-server binary that ships inside Ollama 0.34.0, run directly
set -euo pipefail
B=~/auc-bench
PY=~/.local/share/auc/app/current/backend/venv/bin/python
GGUF=$B/models/nemotron-3.5-lightning-Q4_K_M.gguf
tag=$1; which=$2; shift 2
out=$B/results/$tag; mkdir -p "$out"
PORT=8080
if [ "$which" = upstream ]; then
  BIN=$B/src/llama.cpp/build/bin/llama-server; export LD_LIBRARY_PATH=$B/src/llama.cpp/build/bin
else
  BIN=/usr/local/lib/ollama/llama-server; export LD_LIBRARY_PATH=/usr/local/lib/ollama:/usr/local/lib/ollama/cuda_v13
  # Run outside Ollama, the bundled binary does not find its CUDA backend and silently
  # falls back to the CPU; point it at the library Ollama itself uses.
  export GGML_BACKEND_PATH=/usr/local/lib/ollama/cuda_v13/libggml-cuda.so
fi
# Ollama's own launch flags for this model (from its log), minus port/logging details.
FLAGS=(-m "$GGUF" --host 127.0.0.1 --port $PORT --no-webui -c 8192 -np 1 -ngl 999
       --flash-attn auto -b 2048 -ub 2048 --jinja --metrics)
# Ollama's speculative decoding: the model's built-in MTP heads, 2 draft tokens.
# NO_MTP=1 leaves them off (appending --spec-type none does not override it).
[ "${NO_MTP:-0}" = 1 ] || FLAGS+=(--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-backend-sampling)

# Nothing else on the GPU: unload anything Ollama holds.
for m in $(curl -s localhost:11434/api/ps | python3 -c 'import sys,json; print(" ".join(x["name"] for x in json.load(sys.stdin)["models"]))'); do
  curl -s localhost:11434/api/generate -d "{\"model\":\"$m\",\"keep_alive\":0}" > /dev/null
done
# Cold load: drop this file from the page cache (no root needed for a file we can read).
python3 -c "import os,sys; fd=os.open(sys.argv[1], os.O_RDONLY); os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)" "$GGUF"

{ "$BIN" --version 2>&1 | tail -2; echo "flags: ${FLAGS[*]} $*"; } > "$out/server-info.txt"
t0=$(date +%s.%N)
"$BIN" "${FLAGS[@]}" "$@" > "$out/server.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null || true' EXIT
until curl -sf localhost:$PORT/health > /dev/null; do
  kill -0 $pid 2>/dev/null || { tail -30 "$out/server.log"; exit 1; }
  sleep 0.2
done
t1=$(date +%s.%N)
python3 -c "print('startup_s', round($t1-$t0, 2))" | tee "$out/startup.txt"
curl -s localhost:$PORT/props > "$out/props.json"

for run in single:synthetic-05 batch:all batch-r2:all; do
  r=${run%%:*}; who=${run#*:}; mkdir -p "$out/$r"
  $PY $B/bench_pipeline.py --backend llamacpp --base-url http://127.0.0.1:$PORT \
      --model nemotron-3.5-lightning --residents $who --out "$out/$r" --tag "$tag-$r" | tee "$out/$r/stdout.txt"
  cp "$out/startup.txt" "$out/$r/" 
done
kill $pid; wait $pid 2>/dev/null || true
for r in single batch batch-r2; do cp "$out/server.log" "$out/$r/server.log" 2>/dev/null || true; done
python3 $B/analyze.py "$out/single" "$out/batch" "$out/batch-r2"
