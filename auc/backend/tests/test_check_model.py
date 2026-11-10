"""check-model.sh judges a model on both prompts the app sends: the sub-competency
sections, and an advisor packet's six-competency strengths and growth areas.

A fake model stands in for Ollama, so these check the verdict, not any real model.
"""

import asyncio
import json
import re

import check_model
import pytest
import summary_builder


def _first_comment(prompt):
    return re.search(r"^\d+\. (.+)$", prompt, re.MULTILINE).group(1)


def _fake(competency_reply):
    async def call(client, model, prompt):
        quote = " ".join(_first_comment(prompt).split()[:8])
        if "STRENGTHS" in prompt:
            return competency_reply(quote)
        return json.dumps({"level": 3, "narrative": "Solid work.", "quotes": [quote]})
    return call


def _run(monkeypatch, competency_reply):
    monkeypatch.setattr(summary_builder, "call_ollama", _fake(competency_reply))
    return asyncio.run(check_model.main_async(
        "fake", check_model.TRIALS, show_output=True, use_colour=False))


def test_a_model_that_handles_both_prompts_is_usable(monkeypatch, capsys):
    good = lambda q: json.dumps({  # noqa: E731
        "strengths": {"text": "Strong.", "quotes": [q]},
        "growth_areas": {"text": "Could narrow.", "quotes": [q]},
    })
    assert _run(monkeypatch, good) == 0
    out = capsys.readouterr().out
    assert "Writes packet strengths and growth areas" in out
    assert "for an advisor packet" in out
    assert "Usable." in out


@pytest.mark.parametrize("reply,message", [
    (lambda q: json.dumps({"strengths": {"text": "Strong.", "quotes": ["words nobody wrote"]},
                           "growth_areas": {"text": "Weak.", "quotes": ["also invented"]}}),
     "no statement survived"),
    (lambda q: "The resident is strong in this area and should keep going.",
     "does NOT reliably return the strengths and growth areas JSON"),
])
def test_a_model_that_fails_the_packet_prompt_is_not_usable(monkeypatch, capsys, reply, message):
    assert _run(monkeypatch, reply) == 1
    out = capsys.readouterr().out
    assert message in out
    assert "Not usable for summaries or packets." in out


def test_the_competency_trials_use_the_real_prompt_and_check(monkeypatch):
    prompts = []

    async def call(client, model, prompt):
        prompts.append(prompt)
        return json.dumps({"strengths": {"text": "x", "quotes": [_first_comment(prompt)]},
                           "growth_areas": {"text": "", "quotes": []}})

    monkeypatch.setattr(summary_builder, "call_ollama", call)
    result = asyncio.run(check_model.run_competency_trial(
        None, "fake", check_model.COMPETENCY_TRIALS[0], check_model._silent_logger()))
    assert prompts[0] == summary_builder.build_competency_prompt(
        "Dr. Test Resident", check_model.COMPETENCY_TRIALS[0]["entry"],
        check_model.COMPETENCY_TRIALS[0]["comments"])
    assert (result["status"], result["statements_kept"], result["quotes_kept"]) == ("ok", 1, 1)
