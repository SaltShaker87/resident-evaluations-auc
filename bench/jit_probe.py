"""Is the slow first prompt after a model load runtime compilation of GPU kernels?

Ollama 0.34.0's bundled CUDA 13 library appears to carry only PTX for GB10
(sm_121a), no prebuilt kernels, so the driver must compile them on first use and
caches the result in the CUDA compute cache. This starts llama-server with a
fresh, empty cache and times the first prompts:

  bundled  #1  fresh cache       -> expect a slow first prompt and a cache that fills up
  bundled  #2  same cache again  -> expect the penalty gone
  upstream #1  fresh cache       -> native sm_121a build: expect no penalty, empty cache

    python3 jit_probe.py --out results/jit
"""

import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

B = Path(os.path.expanduser("~/auc-bench"))
GGUF = B / "models/nemotron-3.5-lightning-Q4_K_M.gguf"
BINARIES = {
    "bundled": ("/usr/local/lib/ollama/llama-server", "/usr/local/lib/ollama:/usr/local/lib/ollama/cuda_v13"),
    "upstream": (str(B / "src/llama.cpp/build/bin/llama-server"), str(B / "src/llama.cpp/build/bin")),
}
FLAGS = ["-m", str(GGUF), "--host", "127.0.0.1", "--port", "8081", "--no-webui", "-c", "8192", "-np", "1",
         "-ngl", "999", "--flash-attn", "auto", "-b", "2048", "-ub", "2048",
         "--spec-type", "draft-mtp", "--spec-draft-n-max", "2", "--spec-draft-backend-sampling", "--jinja"]


def post(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:8081{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def healthy():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8081/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def du(path):
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def trial(name, which, cache_dir, prompts, out):
    binary, libpath = BINARIES[which]
    env = dict(os.environ, LD_LIBRARY_PATH=libpath, CUDA_CACHE_PATH=cache_dir)
    if which == "bundled":
        # Outside Ollama the bundled binary does not find its CUDA backend on its own
        # and falls back to the CPU; point it at the library Ollama uses.
        env["GGML_BACKEND_PATH"] = "/usr/local/lib/ollama/cuda_v13/libggml-cuda.so"
    fd = os.open(GGUF, os.O_RDONLY)
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    os.close(fd)
    cache_before = du(cache_dir)
    log = open(out / f"{name}.log", "w")
    t0 = time.perf_counter()
    proc = subprocess.Popen([binary, *FLAGS], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        while not healthy():
            if proc.poll() is not None:
                raise SystemExit(f"{name}: server exited, see {name}.log")
            time.sleep(0.2)
        startup = time.perf_counter() - t0
        calls = []
        for p in prompts:
            body = {"messages": [{"role": "user", "content": p}], "temperature": 0.2, "top_p": 0.95,
                    "response_format": {"type": "json_object"},
                    "chat_template_kwargs": {"enable_thinking": False}}
            t = time.perf_counter()
            j = post("/v1/chat/completions", body)
            tm = j["timings"]
            calls.append({"wall_s": round(time.perf_counter() - t, 3), "prompt_n": tm["prompt_n"],
                          "prompt_ms": round(tm["prompt_ms"], 1),
                          "prompt_tps": round(tm["prompt_per_second"], 1),
                          "predicted_n": tm["predicted_n"], "predicted_tps": round(tm["predicted_per_second"], 1)})
    finally:
        proc.terminate()
        proc.wait()
        log.close()
    row = {"trial": name, "binary": which, "startup_s": round(startup, 2),
           "cache_bytes_before": cache_before, "cache_bytes_after": du(cache_dir), "calls": calls}
    print(json.dumps(row), flush=True)
    return row


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Three prompts AUC really sends (synthetic resident), taken from the Ollama run.
    prompts = [json.loads(line)["prompt"] for line in open(B / "results/ollama-single/calls.jsonl")][:3]
    cache_a = tempfile.mkdtemp(prefix="cuda-cache-bundled-", dir=B)
    cache_b = tempfile.mkdtemp(prefix="cuda-cache-upstream-", dir=B)
    rows = [
        trial("bundled-1-fresh-cache", "bundled", cache_a, prompts, out),
        trial("bundled-2-warm-cache", "bundled", cache_a, prompts, out),
        trial("upstream-1-fresh-cache", "upstream", cache_b, prompts, out),
    ]
    (out / "jit.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
