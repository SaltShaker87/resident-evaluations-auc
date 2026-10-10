# Serving-engine benchmark (synthetic data only)

Times AUC's summary pipeline under Ollama, llama.cpp and vLLM, so a change
(a new Ollama version, a different model, a setting) can be measured instead
of guessed. First run on a DGX Spark on 2026-10-09; the numbers from that run
are in `baseline-2026-10-09.json`.

This folder is for maintainers. It is not part of a release: the release
tarball and the installers are built from `auc/` only.

No real resident data is used anywhere here. The ten residents in
`synthetic_residents.json` are invented, and every run points
`AUC_DATA_DIR` at a scratch folder, so nothing is read from or written to the
real database or validation log.

## What is here

| File | What it does |
|---|---|
| `synthetic_residents.py` / `.json` | 10 invented residents, 12–60 comments each (326 total), seeded so every run gets the same ones |
| `bench_pipeline.py` | Runs AUC's installed summary code, unmodified, on those residents and records every model call's timing fields |
| `run_ollama.sh` | One run against Ollama, keeping Ollama's own log for that window |
| `run_llamacpp.sh` | Serves Ollama's exact model file with `llama-server` (`upstream` build or Ollama's `bundled` binary) and Ollama's flags, then runs one resident and two 10-resident batches. `NO_MTP=1` turns MTP drafting off |
| `run_vllm.sh` | Serves NVIDIA's NVFP4 checkpoint with vLLM 0.27.1 (DGX Spark recipe flags, 8,192 context), same three runs. `MOE_BACKEND=` lets vLLM choose, which MTP needs |
| `truncation_probe.py` | Grows one prompt past the 8,192-token limit to see what Ollama does with it |
| `jit_probe.py` | Times the first prompt with an empty CUDA kernel cache, Ollama's bundled `llama-server` vs an upstream build |
| `capture_setup.sh` | Writes down the machine, Ollama and AUC settings in force |
| `analyze.py` | Summarises one run folder: speeds, time split, truncation flags (writes `summary.json`) |
| `pool.py` | Pools the two batches per engine into the numbers the baseline holds (writes `pooled.json`) |
| `baseline-2026-10-09.json` | The first run's numbers, versions and settings, to compare later runs against |

Run output goes to `results/`, which git ignores: it is large and specific to
one machine.

## What each test needs

All of them assume an AUC install made by the installer
(`~/.local/share/auc/app/current`) and use that install's own Python. For a
manual install, change `APP_BACKEND` at the top of `bench_pipeline.py` and
`PY=` in the `run_*.sh` scripts.

| Test | Needs | Runs on |
|---|---|---|
| Ollama (`run_ollama.sh`) | Ollama with the model pulled | Any AUC machine. Off a Spark, add `--engine ollama` |
| Truncation (`truncation_probe.py`) | Same as above | Any AUC machine (`--engine ollama` off a Spark) |
| llama.cpp (`run_llamacpp.sh`) | llama.cpp built in `src/llama.cpp`; the model file copied out of Ollama's store into `models/` | A GB10 (Spark) as written |
| Kernel cache (`jit_probe.py`) | Same as llama.cpp, plus an `ollama-single` run first (it reuses that run's prompts) | A GB10 (Spark) |
| vLLM (`run_vllm.sh`) | Docker, `vllm/vllm-openai:v0.27.1` (10.5 GB), the NVFP4 checkpoint in `models/hf/` (21.6 GB) | A GB10 (Spark) as written |

The llama.cpp and vLLM scripts always use the Nemotron retrieval engine.

## Running it

The scripts expect to live in `~/auc-bench` on the machine being tested.
From your own computer:

```bash
ssh <machine> 'mkdir -p ~/auc-bench/results'
scp bench/*.py bench/*.sh bench/synthetic_residents.json <machine>:auc-bench/
```

Then on that machine, from `~/auc-bench`. Run one test at a time, with nothing
else using the GPU. The llama.cpp and vLLM scripts unload Ollama's models before
they start, and `--cold` does the same for an Ollama run.

```bash
bash capture_setup.sh > results/setup.txt
bash run_ollama.sh ollama-single --residents synthetic-05 --cold
bash run_ollama.sh ollama-batch --residents all --cold
bash run_ollama.sh ollama-batch-r2 --residents all --cold
cd results && python3 ../pool.py
```

`pool.py` looks for these folder names: `ollama-batch` and `ollama-batch-r2`,
and `<name>/batch` and `<name>/batch-r2` for `llamacpp-upstream`,
`llamacpp-upstream-nomtp`, `llamacpp-bundled`, `vllm-nvfp4-nospec` and
`vllm-nvfp4-mtp2`. Engines whose folders are missing are skipped. The other
engines, if you want them:

```bash
bash run_llamacpp.sh llamacpp-upstream upstream
NO_MTP=1 bash run_llamacpp.sh llamacpp-upstream-nomtp upstream
bash run_llamacpp.sh llamacpp-bundled bundled
bash run_vllm.sh vllm-nvfp4-nospec
MOE_BACKEND= bash run_vllm.sh vllm-nvfp4-mtp2 --speculative-config '{"method":"mtp","num_speculative_tokens":2}'
```

The llama.cpp model file is Ollama's blob, copied out once (it needs sudo,
and it is 25 GB):

```bash
sudo install -m 644 -o $USER -g $USER /usr/share/ollama/.ollama/models/blobs/sha256-<blob> ~/auc-bench/models/nemotron-3.5-lightning-Q4_K_M.gguf
```

`ollama show <model> --modelfile` prints the blob's path on its `FROM` line.
llama.cpp itself is built from source with
`cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121`.

## Comparing with the baseline

Two Ollama batches of the same build agreed within 1.3%, so a change of a few
percent is noise; look for differences larger than that. Compare a new
`results/pooled.json` against `engines` in the baseline. The baseline also
records which versions and settings produced it, so check those first: a
different AUC version can change the prompts, and with them every number.

Pitfalls found the first time round, which the scripts now handle:

- Ollama's bundled `llama-server`, run on its own, silently falls back to the
  CPU unless `GGML_BACKEND_PATH` points at its CUDA library.
- `--spec-type none` appended after `--spec-type draft-mtp` does not turn MTP
  off; it is added to the list. Use `NO_MTP=1`.
- vLLM's Spark recipe flag `--moe-backend marlin` refuses to start with MTP
  on, because the MTP layers are not quantized.
- Copying or downloading large files during a run skews its load times.
