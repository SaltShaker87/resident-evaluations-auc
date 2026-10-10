"""Summarise one or more bench_pipeline.py result folders.

    python analyze.py results/ollama-single results/ollama-batch ...

Prints a table per run and writes summary.json into each folder.

Definitions (the same for every engine):
  reading  time the server spent processing the prompt (Ollama prompt_eval_duration,
           llama.cpp prompt_ms, vLLM time to first token)
  writing  time spent generating the answer (Ollama eval_duration, llama.cpp
           predicted_ms, vLLM first token to last)
  loading  time spent loading the model into memory (Ollama load_duration; for
           llama.cpp and vLLM, which keep the model loaded, server start-up is
           reported separately)
  retrieval  AUC routing comments to sub-competencies (Nemotron embed + rerank
           containers and the ChromaDB index); no LLM involved
  other    everything else in the wall clock: HTTP, the server's own request
           handling, JSON parsing and quote validation in AUC
"""

import json
import re
import statistics
import sys
from pathlib import Path


def load(folder):
    folder = Path(folder)
    calls = [json.loads(line) for line in open(folder / "calls.jsonl")]
    residents = [json.loads(line) for line in open(folder / "residents.jsonl")]
    run = json.loads((folder / "run.json").read_text())
    return folder, run, residents, calls


def mtp_acceptance(folder):
    """Mean draft acceptance from llama-server log lines, if the log was captured."""
    for name in ("ollama-journal.txt", "server.log"):
        p = folder / name
        if p.exists():
            rates = [float(m.group(1)) for m in
                     re.finditer(r"draft acceptance = ([0-9.]+)", p.read_text(errors="replace"))]
            if rates:
                return {"calls": len(rates), "mean": statistics.mean(rates)}
    return None


def truncation_flags(folder):
    for name in ("ollama-journal.txt", "server.log"):
        p = folder / name
        if p.exists():
            text = p.read_text(errors="replace")
            return {
                "slot_truncated_nonzero": len(re.findall(r"truncated = [1-9]", text)),
                "slot_releases": len(re.findall(r"truncated = \d", text)),
                "truncating_input_warnings": len(re.findall(r"truncating input", text, re.I)),
            }
    return None


def _startup(folder):
    """llama.cpp / vLLM: seconds from launching the server to it answering /health."""
    p = folder / "startup.txt"
    if p.exists():
        return float(p.read_text().split()[-1])
    return None


def summarise(folder):
    folder, run, residents, calls = load(folder)
    num_ctx = run["auc_settings"]["num_ctx"]

    p_tok = sum(c["prompt_tokens"] or 0 for c in calls)
    p_s = sum(c["prompt_s"] for c in calls)
    g_tok = sum(c["gen_tokens"] or 0 for c in calls)
    g_s = sum(c["gen_s"] for c in calls)
    load_s = sum(c["load_s"] for c in calls)
    call_wall = sum(c["wall_s"] for c in calls)
    retrieval_s = sum(r["retrieval_s"] or 0 for r in residents)
    wall = run["batch_wall_s"]

    # The first call after a cold load reads its prompt far slower than the rest.
    # Report that penalty on its own rather than letting it hide in "reading".
    warm = [c for c in calls[1:] if c["prompt_s"] > 0]
    warm_read_tps = (sum(c["prompt_tokens"] for c in warm) / sum(c["prompt_s"] for c in warm)) if warm else None
    first = calls[0]
    first_read_penalty = None
    if warm_read_tps and first["prompt_s"] > 0:
        first_read_penalty = max(first["prompt_s"] - first["prompt_tokens"] / warm_read_tps, 0)

    per_call_read = [c["prompt_tokens"] / c["prompt_s"] for c in warm if c["prompt_s"] > 0]
    per_call_write = [c["gen_tokens"] / c["gen_s"] for c in calls if c["gen_s"] > 0 and c["gen_tokens"]]

    prompt_totals = [c.get("prompt_tokens_total") or c["prompt_tokens"] or 0 for c in calls]

    counts = {}
    for r in residents:
        for k, v in (r["counts"] or {}).items():
            counts[k] = counts.get(k, 0) + v

    s = {
        "folder": folder.name,
        "backend": run["backend"], "model": run["model"],
        "residents": len(residents), "comments": sum(r["comments"] for r in residents),
        "calls": len(calls), "retries": sum(1 for c in calls if c["attempt"] == 2),
        "batch_wall_s": wall,
        "per_resident_wall_s": [round(r["wall_s"], 2) for r in residents],
        "mean_resident_wall_s": statistics.mean(r["wall_s"] for r in residents),
        "reading_tps_aggregate": p_tok / p_s if p_s else None,
        "reading_tps_warm": warm_read_tps,
        "reading_tps_median_call": statistics.median(per_call_read) if per_call_read else None,
        "writing_tps_aggregate": g_tok / g_s if g_s else None,
        "writing_tps_median_call": statistics.median(per_call_write) if per_call_write else None,
        "prompt_tokens_total": p_tok, "gen_tokens_total": g_tok,
        "prompt_tokens_max": max(prompt_totals) if prompt_totals else None,
        "prompt_tokens_median": statistics.median(prompt_totals) if prompt_totals else None,
        "gen_tokens_median": statistics.median([c["gen_tokens"] or 0 for c in calls]) if calls else None,
        "num_ctx": num_ctx,
        "time_s": {
            "reading": p_s, "writing": g_s, "loading": load_s,
            "retrieval": retrieval_s,
            "other": wall - p_s - g_s - load_s - retrieval_s,
        },
        "first_call": {k: first.get(k) for k in ("load_s", "prompt_tokens", "prompt_s", "gen_s", "wall_s")},
        "first_read_penalty_s": first_read_penalty,
        "done_reasons": sorted({str(c["done_reason"]) for c in calls}),
        "section_counts": counts,
        "mtp_acceptance": mtp_acceptance(folder),
        "truncation": truncation_flags(folder),
        "call_wall_s": call_wall,
        "server_startup_s": _startup(folder),
    }
    s["share"] = {k: v / wall for k, v in s["time_s"].items()}
    (folder / "summary.json").write_text(json.dumps(s, indent=1))
    return s


def fmt(s):
    t, sh = s["time_s"], s["share"]
    lines = [
        f"== {s['folder']}  [{s['backend']} / {s['model']}]",
        f"   residents={s['residents']} comments={s['comments']} calls={s['calls']} retries={s['retries']}",
        f"   batch wall {s['batch_wall_s']:.1f}s   mean per resident {s['mean_resident_wall_s']:.1f}s   per resident {s['per_resident_wall_s']}",
        f"   reading  {s['reading_tps_warm'] or 0:7.0f} tok/s warm (aggregate {s['reading_tps_aggregate'] or 0:.0f}, median call {s['reading_tps_median_call'] or 0:.0f})",
        f"   writing  {s['writing_tps_aggregate'] or 0:7.1f} tok/s aggregate (median call {s['writing_tps_median_call'] or 0:.1f})",
        f"   tokens   prompt median {s['prompt_tokens_median']} max {s['prompt_tokens_max']} (num_ctx {s['num_ctx']}); gen median {s['gen_tokens_median']}",
        "   time     " + "  ".join(f"{k} {t[k]:.1f}s ({sh[k]*100:.0f}%)" for k in t),
        f"   first call {s['first_call']}  first-read penalty {s['first_read_penalty_s'] or 0:.1f}s",
        f"   done reasons {s['done_reasons']}  sections {s['section_counts']}",
        f"   MTP {s['mtp_acceptance']}  truncation {s['truncation']}  server startup {s['server_startup_s']}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    for f in sys.argv[1:]:
        print(fmt(summarise(f)))
