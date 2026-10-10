#!/bin/bash
# Record the serving setup: what NVIDIA will ask about first.
set +e
echo "## host";        hostname; date -Is; cat /etc/os-release | grep PRETTY; uname -r
echo "## memory";      free -g | head -2
echo "## gpu";         nvidia-smi --query-gpu=name,driver_version,pstate --format=csv,noheader; nvidia-smi | grep "CUDA Version"
echo "## ollama";      ollama --version; echo "binary: $(readlink -f $(which ollama))"
echo "## install";     systemctl is-active ollama; systemctl show ollama -p FragmentPath -p DropInPaths -p Environment
echo "## containers";  docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep -i ollama || echo "no Ollama container (native install)"
echo "## ollama server config (latest start)"
journalctl -u ollama --no-pager -o cat | grep 'msg="server config"' | tail -1 | tr ' ' '\n' | grep -E "^(OLLAMA_(CONTEXT_LENGTH|FLASH_ATTENTION|KEEP_ALIVE|KV_CACHE_TYPE|MAX_LOADED_MODELS|NUM_PARALLEL|MAX_QUEUE|SCHED_SPREAD|VULKAN|LOAD_TIMEOUT)):"
echo "## llama-server launched by ollama for the production model (latest)"
journalctl -u ollama --no-pager -o cat | grep 'starting llama-server' | grep 5c19f62 | tail -1 | sed -E 's/.*cmd=//'
echo "## bundled llama-server";  /usr/local/lib/ollama/llama-server --version 2>&1 | tail -2
echo "## models";      ollama list
echo "## production model"; ollama show nemotron-3.5-lightning:latest
ollama show nemotron-3.5-lightning:latest --modelfile | grep -E '^(FROM|RENDERER|PARSER|PARAMETER)'
echo "## AUC";         cat ~/.local/share/auc/app/current/VERSION 2>/dev/null; grep -E '^(OLLAMA_|AUC_EMBED|AUC_NIM|AUC_RETRIEVAL)' ~/.config/auc/auc.env
grep -nE '^(OLLAMA_TIMEOUT_SECONDS|OLLAMA_OPTIONS|OLLAMA_THINK|USE_OLLAMA_JSON_FORMAT)' ~/.local/share/auc/app/current/backend/summary_builder.py
echo "## retrieval containers"; docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep nemotron
