"""Where does AUC's 8192-token context run out, and what does Ollama do then?

Builds the production prompt for one sub-competency (real ACGME descriptor from
the index, synthetic comments) with a growing number of comments, sends each
with AUC's exact request, and records prompt tokens, done_reason and whether
the answer still parses and its quotes still validate.

    ~/.local/share/auc/app/current/backend/venv/bin/python truncation_probe.py --out results/truncation
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

APP_BACKEND = Path(os.path.expanduser("~/.local/share/auc/app/current/backend"))
os.environ["AUC_DATA_DIR"] = os.path.expanduser("~/auc-bench/scratch-data")
sys.path.insert(0, str(APP_BACKEND))
sys.path.insert(0, str(Path(__file__).parent))

import httpx  # noqa: E402
import rag_retrieval  # noqa: E402
import summary_builder  # noqa: E402
from synthetic_residents import KEYWORDED  # noqa: E402

SUB = {"id": "PROF1", "name": "Professionalism 1: Professional Behavior",
       "domain": "PROF", "domain_name": "Professionalism"}


async def main(args):
    collection = rag_retrieval.open_collection(args.engine)
    milestones, supplemental = rag_retrieval.fetch_chunks(collection, SUB["id"])
    descriptor = "\n\n".join(p for p in (milestones, supplemental) if p)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    async with httpx.AsyncClient(timeout=600) as client:
        for n in args.sizes:
            comments = [{"text": f"{KEYWORDED[i % len(KEYWORDED)]} (week {i + 1})", "label": "synthetic"}
                        for i in range(n)]
            prompt = summary_builder.build_prompt("Synthetic Resident 99, PGY-2", SUB, descriptor, comments)
            payload = {"model": args.model, "prompt": prompt, "stream": False,
                       "think": summary_builder.OLLAMA_THINK, "options": summary_builder.OLLAMA_OPTIONS,
                       "format": "json"}
            t0 = time.perf_counter()
            r = await client.post(f"{args.base_url}/api/generate", json=payload)
            wall = time.perf_counter() - t0
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text}
            raw = j.get("response", "")
            try:
                parsed = summary_builder.parse_model_json(raw)
                section = summary_builder.validate_section(
                    SUB, parsed, comments, summary_builder.get_logger(), "[probe]")
                status = section["status"]
            except Exception as e:
                status = f"parse_failed: {e}"
            row = {"comments": n, "prompt_chars": len(prompt), "http": r.status_code,
                   "prompt_eval_count": j.get("prompt_eval_count"), "eval_count": j.get("eval_count"),
                   "done_reason": j.get("done_reason"), "error": j.get("error"),
                   "wall_s": round(wall, 2), "section_status": status,
                   "prompt_mentions_sub": "SUB-COMPETENCY: PROF1" in prompt}
            rows.append(row)
            print(json.dumps(row), flush=True)
    (out / "probe.json").write_text(json.dumps({"descriptor_chars": len(descriptor), "rows": rows}, indent=1))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:11434")
    p.add_argument("--model", default="nemotron-3.5-lightning")
    p.add_argument("--engine", default="nemotron")
    p.add_argument("--sizes", type=int, nargs="+", default=[5, 25, 50, 100, 150, 175, 200, 250, 300])
    p.add_argument("--out", required=True)
    asyncio.run(main(p.parse_args()))
