#!/bin/bash
# Run bench_pipeline.py against Ollama and keep the server log for the window.
#   bash run_ollama.sh <tag> [bench_pipeline args...]
set -euo pipefail
B=~/auc-bench
PY=~/.local/share/auc/app/current/backend/venv/bin/python
MODEL=${MODEL:-nemotron-3.5-lightning}
tag=$1; shift
out=$B/results/$tag
mkdir -p "$out"
start=$(date '+%Y-%m-%d %H:%M:%S')
{ ollama ps; echo; free -m; echo; uptime; } > "$out/before.txt"
$PY $B/bench_pipeline.py --backend ollama --model "$MODEL" --out "$out" --tag "$tag" "$@" | tee "$out/stdout.txt"
sleep 1
{ ollama ps; echo; free -m; } > "$out/after.txt"
journalctl -u ollama --since "$start" --no-pager -o cat > "$out/ollama-journal.txt"
python3 $B/analyze.py "$out"
