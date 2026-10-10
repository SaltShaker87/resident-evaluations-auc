"""Time AUC's real summary pipeline against a serving engine, on synthetic residents.

Runs on the Spark with the installed app's own Python:

    ~/.local/share/auc/app/current/backend/venv/bin/python bench_pipeline.py \
        --backend ollama --model nemotron-3.5-lightning --residents synthetic-05 \
        --out results/ollama-single

What runs is the production code, unmodified: summary_builder.generate_report
from the installed app (routing, prompts, num_ctx, JSON format, the one retry,
quote validation). Only two things are touched:

  * AUC_DATA_DIR points at a scratch folder, so the synthetic runs never write
    into the real validation log or database.
  * The one network call to the model is observed (Ollama) or swapped for an
    adapter that sends the same prompt with the same settings (llama.cpp,
    vLLM). The adapter is the only code that differs between engines.

Writes calls.jsonl (one line per model call, with the engine's timing fields and
the prompt), residents.jsonl (one line per resident) and run.json.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

APP_BACKEND = Path(os.path.expanduser("~/.local/share/auc/app/current/backend"))
SCRATCH = Path(os.path.expanduser("~/auc-bench/scratch-data"))
SCRATCH.mkdir(parents=True, exist_ok=True)
os.environ["AUC_DATA_DIR"] = str(SCRATCH)  # must be set before config is imported
sys.path.insert(0, str(APP_BACKEND))

import httpx  # noqa: E402
import summary_builder  # noqa: E402

CALLS = []
CTX = {"resident": None}
SUB_RE = re.compile(r"SUB-COMPETENCY: (\S+)")


def _call_meta(prompt):
    m = SUB_RE.search(prompt)
    return {
        "resident": CTX["resident"],
        "sub_id": m.group(1) if m else None,
        "attempt": 2 if prompt.endswith(summary_builder._RETRY_SUFFIX) else 1,
        "prompt": prompt,
        "prompt_chars": len(prompt),
    }


# --------------------------------------------------------------------------
# Ollama: production call_ollama runs untouched; we observe the response.
# --------------------------------------------------------------------------
_orig_post = httpx.AsyncClient.post


async def _observing_post(self, url, *args, **kwargs):
    t0 = time.perf_counter()
    response = await _orig_post(self, url, *args, **kwargs)
    t1 = time.perf_counter()
    if str(url).endswith("/api/generate"):
        body = kwargs.get("json") or {}
        rec = _call_meta(body.get("prompt", ""))
        rec["request_options"] = {k: v for k, v in body.items() if k != "prompt"}
        rec["wall_s"] = t1 - t0
        rec["http_status"] = response.status_code
        try:
            j = response.json()
        except Exception:
            j = {}
        ns = 1e9
        rec.update({
            "prompt_tokens": j.get("prompt_eval_count"),
            "prompt_s": (j.get("prompt_eval_duration") or 0) / ns,
            "gen_tokens": j.get("eval_count"),
            "gen_s": (j.get("eval_duration") or 0) / ns,
            "load_s": (j.get("load_duration") or 0) / ns,
            "server_total_s": (j.get("total_duration") or 0) / ns,
            "done_reason": j.get("done_reason"),
            "response_chars": len(j.get("response", "") or ""),
            "raw_timing": {k: j.get(k) for k in (
                "prompt_eval_count", "prompt_eval_duration", "eval_count",
                "eval_duration", "load_duration", "total_duration")},
        })
        CALLS.append(rec)
    return response


# --------------------------------------------------------------------------
# llama.cpp (llama-server) and vLLM: OpenAI-compatible chat endpoint.
# Same prompt as one user turn, same temperature, JSON-object output,
# thinking off (AUC sends think=false to Ollama), context capped by the server.
# --------------------------------------------------------------------------
def make_openai_adapter(base_url, backend, top_p):
    async def call_model(client, model, prompt):
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": summary_builder.OLLAMA_OPTIONS.get("temperature", 0.2),
            "top_p": top_p,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        rec = _call_meta(prompt)
        rec["request_options"] = {k: v for k, v in payload.items() if k != "messages"}

        if backend == "llamacpp":
            # Non-streaming: llama-server reports its own timings in the response.
            t0 = time.perf_counter()
            r = await client.post(f"{base_url}/v1/chat/completions", json=payload)
            t1 = time.perf_counter()
            r.raise_for_status()
            j = r.json()
            tm = j.get("timings", {}) or {}
            usage = j.get("usage", {}) or {}
            text = j["choices"][0]["message"].get("content") or ""
            rec.update({
                "wall_s": t1 - t0,
                "http_status": r.status_code,
                "prompt_tokens": tm.get("prompt_n"),
                "prompt_tokens_total": usage.get("prompt_tokens"),
                "prompt_s": (tm.get("prompt_ms") or 0) / 1000,
                "gen_tokens": tm.get("predicted_n"),
                "gen_s": (tm.get("predicted_ms") or 0) / 1000,
                "load_s": 0.0,
                "server_total_s": ((tm.get("prompt_ms") or 0) + (tm.get("predicted_ms") or 0)) / 1000,
                "done_reason": j["choices"][0].get("finish_reason"),
                "response_chars": len(text),
                "raw_timing": tm,
            })
            CALLS.append(rec)
            return text

        # vLLM: stream, so time-to-first-token separates reading from writing.
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        t0 = time.perf_counter()
        t_first = None
        parts = []
        usage = {}
        finish = None
        async with client.stream("POST", f"{base_url}/v1/chat/completions", json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for ch in chunk.get("choices", []):
                    delta = ch.get("delta", {}) or {}
                    piece = delta.get("content") or ""
                    if piece or delta.get("reasoning_content") or delta.get("reasoning"):
                        if t_first is None:
                            t_first = time.perf_counter()
                    parts.append(piece)
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
        t1 = time.perf_counter()
        t_first = t_first or t1
        ctoks = usage.get("completion_tokens") or 0
        text = "".join(parts)
        rec.update({
            "wall_s": t1 - t0,
            "http_status": 200,
            "prompt_tokens": usage.get("prompt_tokens"),
            "prompt_tokens_total": usage.get("prompt_tokens"),
            "cached_tokens": ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")),
            # TTFT covers prefill plus the first generated token.
            "prompt_s": t_first - t0,
            "gen_tokens": max(ctoks - 1, 0),
            "gen_s": t1 - t_first,
            "load_s": 0.0,
            "server_total_s": t1 - t0,
            "done_reason": finish,
            "response_chars": len(text),
            "raw_timing": {"ttft_s": t_first - t0, "usage": usage},
        })
        CALLS.append(rec)
        return text

    return call_model


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------
def ollama_unload_all(base_url):
    """Cold start: unload every model Ollama has resident."""
    ps = httpx.get(f"{base_url}/api/ps", timeout=10).json().get("models", [])
    for m in ps:
        httpx.post(f"{base_url}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=60)
    for _ in range(60):
        if not httpx.get(f"{base_url}/api/ps", timeout=10).json().get("models"):
            return [m["name"] for m in ps]
        time.sleep(1)
    raise SystemExit("Ollama did not unload its models")


async def run(args):
    data = json.loads(Path(args.data).read_text())
    wanted = args.residents.split(",") if args.residents != "all" else None
    residents = [r for r in data["residents"] if wanted is None or r["id"] in wanted]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.backend == "ollama":
        summary_builder.OLLAMA_URL = args.base_url
        httpx.AsyncClient.post = _observing_post
        unloaded = ollama_unload_all(args.base_url) if args.cold else []
    else:
        summary_builder.call_ollama = make_openai_adapter(args.base_url, args.backend, args.top_p)
        unloaded = []

    # Wrap retrieval so its time is measured separately from the model calls.
    orig_prepare = summary_builder._prepare_sync
    retrieval = {}

    def timed_prepare(comments, engine):
        t0 = time.perf_counter()
        result = orig_prepare(comments, engine)
        retrieval[CTX["resident"]] = time.perf_counter() - t0
        return result

    summary_builder._prepare_sync = timed_prepare

    run_meta = {
        "backend": args.backend, "model": args.model, "base_url": args.base_url,
        "engine": args.engine, "residents": [r["id"] for r in residents],
        "cold_start": args.cold, "unloaded_before_start": unloaded,
        "tag": args.tag,
        "auc_version": (APP_BACKEND.parent / "VERSION").read_text().strip()
        if (APP_BACKEND.parent / "VERSION").exists() else None,
        "auc_settings": {
            "num_ctx": summary_builder.OLLAMA_OPTIONS.get("num_ctx"),
            "options": summary_builder.OLLAMA_OPTIONS,
            "think": summary_builder.OLLAMA_THINK,
            "format_json": summary_builder.USE_OLLAMA_JSON_FORMAT,
            "keep_alive_sent": None,  # AUC does not send keep_alive
            "timeout_s": summary_builder.OLLAMA_TIMEOUT_SECONDS,
            "sequential_calls": True,
        },
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    batch_t0 = time.perf_counter()
    rows = []
    for r in residents:
        CTX["resident"] = r["id"]
        n_before = len(CALLS)
        t0 = time.perf_counter()
        done = None
        async for event, payload in summary_builder.generate_report(
            r["id"], r["label"], r["comments"], model=args.model, engine=args.engine
        ):
            if event == "done":
                done = payload
            elif event == "error":
                done = {"error": payload}
        wall = time.perf_counter() - t0
        calls = CALLS[n_before:]
        row = {
            "resident": r["id"], "label": r["label"], "comments": len(r["comments"]),
            "wall_s": wall, "retrieval_s": retrieval.get(r["id"]),
            "calls": len(calls),
            "retries": sum(1 for c in calls if c["attempt"] == 2),
            "counts": (done or {}).get("counts"), "error": (done or {}).get("error"),
        }
        rows.append(row)
        print(json.dumps({k: row[k] for k in ("resident", "comments", "wall_s", "retrieval_s", "calls", "retries", "counts")}), flush=True)

    run_meta["batch_wall_s"] = time.perf_counter() - batch_t0
    run_meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    with open(out / "calls.jsonl", "w") as f:
        for c in CALLS:
            f.write(json.dumps(c) + "\n")
    with open(out / "residents.jsonl", "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    (out / "run.json").write_text(json.dumps(run_meta, indent=1))
    print(json.dumps({"batch_wall_s": run_meta["batch_wall_s"], "calls": len(CALLS)}))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--backend", choices=["ollama", "llamacpp", "vllm"], required=True)
    p.add_argument("--base-url", default="http://localhost:11434")
    p.add_argument("--model", required=True)
    p.add_argument("--engine", default="nemotron", help="AUC retrieval engine in force")
    p.add_argument("--residents", default="all", help="comma-separated ids, or all")
    p.add_argument("--data", default=str(Path(__file__).with_name("synthetic_residents.json")))
    p.add_argument("--out", required=True)
    p.add_argument("--cold", action="store_true", help="Ollama: unload models first")
    p.add_argument("--top-p", type=float, default=0.95, help="non-Ollama: Ollama's Modelfile top_p")
    p.add_argument("--tag", default="")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
