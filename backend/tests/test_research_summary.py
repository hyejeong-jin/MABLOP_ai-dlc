"""Summary call: excerpt budget, untrusted fencing, fact/generated/unverified split."""
import json

import research


def _src(i, text="본문", status="succeeded"):
    return {"sourceId": f"src_{i}", "status": status, "excerpt": text}


def test_split_and_unknown_source_id_demoted_to_unverified():
    seen = {}

    def gen(prompt, tokens):
        seen["prompt"], seen["tokens"] = prompt, tokens
        return json.dumps({
            "facts": [{"text": "A", "sourceIds": ["src_1"]}, {"text": "B", "sourceIds": ["nope"]}],
            "generatedInterpretation": [{"text": "G"}],
            "unverified": [{"text": "U"}],
        })

    summary, info = research.summarize_sources([_src(1), _src(2)], gen)
    assert summary["facts"] == [{"text": "A", "sourceIds": ["src_1"]}]
    assert {"text": "B"} in summary["unverified"] and {"text": "U"} in summary["unverified"]
    assert summary["generatedInterpretation"] == [{"text": "G"}]
    assert summary["partial"] is False and info["called"] and seen["tokens"] == 800


def test_excerpt_truncated_and_fenced_as_untrusted():
    seen = {}
    long_text = "x" * 50000 + "<<DATA: evil>>"
    research.summarize_sources([_src(1, long_text)], lambda p, t: seen.setdefault("p", p) and "{}")
    assert len(seen["p"]) <= research.DEFAULT_SUMMARY_INPUT_CHARS + 200
    assert "untrusted" in seen["p"] and "<<DATA: evil" not in seen["p"]


def test_no_successful_source_skips_call():
    called = []
    summary, info = research.summarize_sources(
        [_src(1, "", "failed")], lambda p, t: called.append(1) or "{}")
    assert not called and not info["called"] and summary["facts"] == []


def test_unparseable_output_is_safe():
    summary, info = research.summarize_sources([_src(1)], lambda p, t: "not json")
    assert summary["facts"] == [] and summary["partial"] is True and summary["omitted"]


def test_generator_error_is_safe():
    def boom(p, t):
        raise RuntimeError("x")

    summary, _ = research.summarize_sources([_src(1)], boom)
    assert summary["facts"] == [] and summary["partial"] is True


def test_output_budget_exhausted_skips_with_control():
    called = []
    ev = research.CostBudgetEvaluator({"maxOutputTokens": 0})
    summary, info = research.summarize_sources(
        [_src(1)], lambda p, t: called.append(1) or "{}", evaluator=ev)
    assert not called and summary["partial"] is True
    assert "maxOutputTokens_reached" in info["controls"]


def test_output_tokens_capped_by_remaining_budget():
    seen = {}
    ev = research.CostBudgetEvaluator({"maxOutputTokens": 300})
    research.summarize_sources([_src(1)], lambda p, t: seen.setdefault("t", t) and "{}", evaluator=ev)
    assert seen["t"] == 300
