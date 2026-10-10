"""Pool the two 10-resident batches per engine into the numbers the one-pager uses.

    python3 pool.py            (run inside ~/auc-bench/results)
"""

import json
from pathlib import Path

RUNS = {
    "Ollama 0.34.0 (production)": ["ollama-batch", "ollama-batch-r2"],
    "llama.cpp upstream, MTP on": ["llamacpp-upstream/batch", "llamacpp-upstream/batch-r2"],
    "llama.cpp upstream, MTP off": ["llamacpp-upstream-nomtp/batch", "llamacpp-upstream-nomtp/batch-r2"],
    "Ollama's llama-server run directly": ["llamacpp-bundled/batch", "llamacpp-bundled/batch-r2"],
    "vLLM NVFP4, no speculation": ["vllm-nvfp4-nospec/batch", "vllm-nvfp4-nospec/batch-r2"],
    "vLLM NVFP4, MTP 2": ["vllm-nvfp4-mtp2/batch", "vllm-nvfp4-mtp2/batch-r2"],
}


def jl(path):
    return [json.loads(line) for line in open(path)]


out = {}
for name, folders in RUNS.items():
    if not all(Path(f, "summary.json").exists() for f in folders):
        print(f"{name}: not complete")
        continue
    summaries = [json.load(open(Path(f, "summary.json"))) for f in folders]
    calls = [c for f in folders for c in jl(Path(f, "calls.jsonl"))]
    residents = [r for f in folders for r in jl(Path(f, "residents.jsonl"))]
    warm = [c for c in calls if c["load_s"] <= 1]  # leave out the call that paid a cold load
    walls = [s["batch_wall_s"] for s in summaries]
    total = sum(walls)
    t = {k: sum(s["time_s"][k] for s in summaries) for k in summaries[0]["time_s"]}
    r05 = [r["wall_s"] for r in residents if r["resident"] == "synthetic-05"]
    row = {
        "batches_s": [round(w, 1) for w in walls],
        "batch_avg_s": round(total / len(walls), 1),
        "per_resident_avg_s": round(sum(r["wall_s"] for r in residents) / len(residents), 1),
        "resident05_avg_s": round(sum(r05) / len(r05), 1),
        "reading_tps": round(sum(c["prompt_tokens"] for c in warm) / sum(c["prompt_s"] for c in warm)),
        "writing_tps": round(sum(c["gen_tokens"] for c in calls) / sum(c["gen_s"] for c in calls), 1),
        "share_pct": {k: round(100 * v / total, 1) for k, v in t.items()},
        "loading_s_per_batch": round(t["loading"] / len(walls), 1),
        "gen_tokens_median": sorted(c["gen_tokens"] for c in calls)[len(calls) // 2],
        "prompt_tokens_max": max(c.get("prompt_tokens_total") or c["prompt_tokens"] for c in calls),
        "calls": len(calls),
        "retries": sum(1 for c in calls if c["attempt"] == 2),
        "sections_ok": sum((r["counts"] or {}).get("ok", 0) for r in residents),
    }
    out[name] = row
    print(f"{name:36} {json.dumps(row)}")

for f in ["ollama-single", "llamacpp-upstream/single", "llamacpp-bundled/single",
          "vllm-nvfp4-nospec/single", "vllm-nvfp4-mtp2/single", "llamacpp-upstream-nomtp/single"]:
    p = Path(f, "summary.json")
    if p.exists():
        s = json.load(open(p))
        print(f"single {f:32} wall {s['batch_wall_s']:.1f}s  load {s['time_s']['loading']:.1f}s  "
              f"startup {s.get('server_startup_s')}  first-read penalty {s['first_read_penalty_s'] or 0:.1f}s  "
              f"share {({k: round(v * 100) for k, v in s['share'].items()})}")

Path("pooled.json").write_text(json.dumps(out, indent=1))
