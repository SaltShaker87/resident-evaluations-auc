"""Test whether a model can actually write summaries, before you trust it with one.

    bash auc/check-model.sh nemotron:latest

Any model in `ollama list` can be selected in Settings, but only some of them
work. The generator depends on two behaviours that are not guaranteed and that
no amount of reading a model card will tell you:

  1. **JSON-constrained output.** Every call sets Ollama's `format: json`. A
     model that answers in prose instead has every section marked
     `generation_failed`.

  2. **Verbatim quoting.** Every quote is checked character-for-character
     against the comments routed to that sub-competency. A model that
     paraphrases has its quotes dropped, and a section with no surviving quotes
     has its narrative discarded — so the report comes back empty even though
     the model was working. That validation is what keeps invented evidence out
     of a resident's record; the right response to a model failing it is a
     different model, not weaker validation.

This runs the real code path — the app's own prompt builder, Ollama call,
parser and validator — against made-up committee comments, so what it measures
is what will happen. It touches no database and writes no log.
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
import summary_builder  # noqa: E402
from config import OLLAMA_URL  # noqa: E402

# Made-up comments in the register real ones are written in, each with at least
# one distinctive phrase. Distinctive matters: it is how a copied quote is told
# apart from a plausible-sounding invention.
TRIALS = [
    {
        "entry": {
            "id": "PC2",
            "name": "Develops and achieves comprehensive management plan for each patient",
            "domain": "PC",
            "domain_name": "Patient Care",
        },
        "descriptor": (
            "Level 3: Develops and achieves a comprehensive management plan for "
            "patients with common complaints and conditions."
        ),
        "comments": [
            {"text": "On the wards she anticipated the diuresis plan two days out and "
                     "had already arranged the electrolyte checks before I asked.",
             "label": "Committee note"},
            {"text": "Management plans are thorough but occasionally over-inclusive; "
                     "she orders a wide net of tests early.",
             "label": "Committee note"},
            {"text": "Handled a complicated discharge on a patient with three competing "
                     "problems and kept the plan coherent throughout.",
             "label": "MedHub comment"},
        ],
    },
    {
        "entry": {
            "id": "PROF1",
            "name": "Professional behavior and ethical principles",
            "domain": "PROF",
            "domain_name": "Professionalism",
        },
        "descriptor": (
            "Level 3: Demonstrates professional behavior and ethical principles "
            "in complex or stressful situations."
        ),
        "comments": [
            {"text": "Stayed forty minutes past sign-out to finish a family conversation "
                     "rather than hand it to the night team.",
             "label": "Committee note"},
            {"text": "Acknowledged a missed lab result directly to the attending without "
                     "being prompted, which the committee noted approvingly.",
             "label": "Committee note"},
        ],
    },
    {
        "entry": {
            "id": "ICS1",
            "name": "Patient- and family-centered communication",
            "domain": "ICS",
            "domain_name": "Interpersonal and Communication Skills",
        },
        "descriptor": (
            "Level 3: Uses patient- and family-centered communication in "
            "straightforward and difficult encounters."
        ),
        "comments": [
            {"text": "Ran a goals-of-care discussion with a daughter who had been "
                     "hostile to the previous team and left her visibly reassured.",
             "label": "Committee note"},
            {"text": "Tends to use hedging language with families when the prognosis is "
                     "uncertain; feedback given to be more direct.",
             "label": "MedHub comment"},
        ],
    },
]

OK = "\033[32m"
BAD = "\033[31m"
WARN = "\033[33m"
DIM = "\033[2m"
OFF = "\033[0m"


def colour(enabled):
    if enabled:
        return OK, BAD, WARN, DIM, OFF
    return "", "", "", "", ""


def _silent_logger():
    """validate_section logs its decisions; here they go nowhere.

    The real log is research provenance for the study — a dry run must not
    write into it.
    """
    logger = logging.getLogger("auc.check_model")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


async def run_trial(client, model, trial, logger):
    """One sub-competency, exactly as the app would do it."""
    prompt = summary_builder.build_prompt(
        "Dr. Test Resident", trial["entry"], trial["descriptor"], trial["comments"]
    )

    started = time.monotonic()
    try:
        raw = await summary_builder.call_ollama(client, model, prompt)
    except httpx.HTTPStatusError as e:
        return {"error": f"Ollama returned {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"Could not reach Ollama: {e}"}
    elapsed = time.monotonic() - started

    result = {"elapsed": elapsed, "raw": raw, "id": trial["entry"]["id"]}

    try:
        parsed = summary_builder.parse_model_json(raw)
    except ValueError as e:
        result["json_ok"] = False
        result["json_error"] = str(e)
        return result

    result["json_ok"] = True
    result["level"] = summary_builder._coerce_level(parsed.get("level"))

    quotes = parsed.get("quotes")
    if isinstance(quotes, str):
        quotes = [quotes]
    elif not isinstance(quotes, list):
        quotes = []
    result["quotes_offered"] = len(quotes)

    section = summary_builder.validate_section(
        trial["entry"], parsed, trial["comments"], logger, "[check-model]"
    )
    result["quotes_kept"] = len(section["quotes"])
    result["status"] = section["status"]
    result["narrative"] = section.get("narrative") or ""

    # The quotes it offered that were not exact copies — the interesting ones.
    result["rejected"] = [
        q for q in quotes
        if isinstance(q, str) and summary_builder.find_verbatim(q, trial["comments"]) is None
    ]
    return result


async def main_async(model, trials, show_output, use_colour):
    ok, bad, warn, dim, off = colour(use_colour)
    logger = _silent_logger()

    print()
    print(f"  Testing model: {model}")
    print(f"  Ollama: {OLLAMA_URL}")
    print(f"  {dim}Running {len(trials)} sub-competency generations, the same way the app "
          f"does.{off}")
    print(f"  {dim}A large model can take a minute or more per section.{off}")
    print()

    results = []
    # Same timeout the generator uses, so a model that is merely slow here is
    # a model that is merely slow there.
    async with httpx.AsyncClient(timeout=summary_builder.OLLAMA_TIMEOUT_SECONDS) as client:
        for index, trial in enumerate(trials, start=1):
            label = f"{trial['entry']['id']} — {trial['entry']['name'][:48]}"
            print(f"  [{index}/{len(trials)}] {label} ... ", end="", flush=True)
            result = await run_trial(client, model, trial, logger)
            results.append(result)

            if "error" in result:
                print(f"{bad}failed{off}")
                print(f"      {result['error']}")
                return 1
            if not result["json_ok"]:
                print(f"{bad}not JSON{off}  ({result['elapsed']:.1f}s)")
            elif result["quotes_kept"] == 0:
                print(f"{bad}no quotes survived{off}  ({result['elapsed']:.1f}s)")
            else:
                print(f"{ok}ok{off}  ({result['elapsed']:.1f}s, "
                      f"{result['quotes_kept']}/{result['quotes_offered']} quotes verbatim)")

    print()

    # --- Requirement 1: JSON -------------------------------------------
    json_ok = sum(1 for r in results if r.get("json_ok"))
    print(f"  {'─' * 66}")
    if json_ok == len(results):
        print(f"  {ok}✓{off} Honours JSON-constrained output  "
              f"{dim}({json_ok}/{len(results)} replies parsed){off}")
    else:
        print(f"  {bad}✗{off} Does NOT reliably honour JSON-constrained output  "
              f"{dim}({json_ok}/{len(results)} parsed){off}")
        for r in results:
            if not r.get("json_ok"):
                print(f"      {r['id']}: {r.get('json_error')}")
                print(f"      {dim}returned: {r['raw'][:160]!r}{off}")
        print(f"      {dim}Every section would be marked generation_failed. "
              f"Use a different model.{off}")

    # --- Requirement 2: verbatim quoting --------------------------------
    offered = sum(r.get("quotes_offered", 0) for r in results)
    kept = sum(r.get("quotes_kept", 0) for r in results)
    sections_ok = sum(1 for r in results if r.get("status") == summary_builder.STATUS_OK)

    if json_ok == 0:
        print(f"  {dim}·{off} Verbatim quoting could not be assessed — no reply parsed.")
    elif sections_ok == len(results) and offered and kept / offered >= 0.8:
        print(f"  {ok}✓{off} Quotes verbatim  "
              f"{dim}({kept}/{offered} snippets were exact copies; "
              f"{sections_ok}/{len(results)} sections kept their narrative){off}")
    elif sections_ok == len(results):
        # Every section survived, but evidence is being thrown away on the way.
        # Reports come out thinner than the notes justify, which is easy to
        # mistake for the residents having less written about them.
        print(f"  {warn}⚠{off} Quotes only sometimes verbatim  "
              f"{dim}({kept}/{offered} snippets were exact copies, but all "
              f"{sections_ok} sections kept their narrative){off}")
        print(f"      {dim}Usable. Reports will be thinner than the notes justify, "
              f"because rejected snippets are evidence that never reaches you.{off}")
    elif sections_ok > 0:
        print(f"  {warn}⚠{off} Quotes only sometimes verbatim  "
              f"{dim}({kept}/{offered} exact; "
              f"{len(results) - sections_ok} of {len(results)} sections discarded){off}")
        print(f"      {dim}Usable, but expect gaps in reports. A larger model usually "
              f"fixes this.{off}")
    else:
        print(f"  {bad}✗{off} Does NOT quote verbatim  "
              f"{dim}({kept}/{offered} exact){off}")
        print(f"      {dim}Every narrative would be discarded and the report would come "
              f"back empty.{off}")

    for r in results:
        for rejected in r.get("rejected", [])[:2]:
            print(f"      {dim}{r['id']} paraphrased: {rejected[:110]!r}{off}")

    # --- Levels ----------------------------------------------------------
    levels = [r.get("level") for r in results if r.get("json_ok")]
    valid_levels = [lv for lv in levels if lv is not None]
    if levels and len(valid_levels) == len(levels):
        print(f"  {ok}✓{off} Suggests usable milestone levels  {dim}({valid_levels}){off}")
    elif levels:
        print(f"  {warn}⚠{off} Some replies had no usable milestone level  {dim}({levels}){off}")

    # --- Speed -----------------------------------------------------------
    times = [r["elapsed"] for r in results if "elapsed" in r]
    if times:
        per_section = sum(times) / len(times)
        full_run = per_section * 21
        print(f"  {dim}·{off} {per_section:.1f}s per section  →  about "
              f"{full_run / 60:.1f} minutes for a full 21-section report")
        if full_run > 900:
            print(f"      {warn}That is slow enough to be awkward in a live meeting.{off}")

    if show_output:
        print(f"\n  {dim}{'─' * 66}{off}")
        for r in results:
            print(f"\n  {r['id']} narrative:")
            print(f"    {r.get('narrative') or '(discarded)'}")

    print()
    fatal = json_ok < len(results) or sections_ok == 0
    if fatal:
        print(f"  {bad}Not usable for summaries.{off} Try a different model — and do not "
              f"relax the")
        print("  quote validation to accommodate this one; it is what keeps invented")
        print("  evidence out of a resident's record.")
        print()
        return 1

    print(f"  {ok}Usable.{off} Confirm on a real resident with real notes before a meeting")
    print("  depends on it, then record the choice in auc/README.md.")
    print()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Check whether a model can write summaries the generator will accept.",
    )
    parser.add_argument("model", nargs="?", help="Ollama model name, e.g. nemotron:latest")
    parser.add_argument("--trials", type=int, default=len(TRIALS),
                        help=f"how many sub-competencies to try (1-{len(TRIALS)})")
    parser.add_argument("--show-output", action="store_true",
                        help="print the narratives the model produced")
    parser.add_argument("--no-colour", action="store_true")
    args = parser.parse_args()

    model = args.model or summary_builder.SUMMARY_MODEL
    trials = TRIALS[:max(1, min(args.trials, len(TRIALS)))]
    use_colour = sys.stdout.isatty() and not args.no_colour

    # Fail early and clearly rather than timing out per trial.
    try:
        tags = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0).json()
    except Exception as e:
        print(f"\n  Could not reach Ollama at {OLLAMA_URL}: {e}\n")
        return 1

    installed = [m["name"] for m in tags.get("models", [])]
    if model not in installed:
        print(f"\n  '{model}' is not installed. Ollama has:")
        for name in installed:
            print(f"    {name}")
        print(f"\n  Pull it first: ollama pull {model}\n")
        return 1

    return asyncio.run(main_async(model, trials, args.show_output, use_colour))


if __name__ == "__main__":
    sys.exit(main())
