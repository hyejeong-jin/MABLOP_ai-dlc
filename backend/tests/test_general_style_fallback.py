import importlib
import pytest
import prompt_builder
import research_store
import voc_store


@pytest.fixture
def de(monkeypatch, s3_bucket, signing_secret):
    monkeypatch.setenv("MABLOP_TEXT_MODEL", "test-model")
    monkeypatch.setenv("MABLOP_EMBED_MODEL", "test-embed")
    import drafting_engine
    importlib.reload(drafting_engine)
    return drafting_engine


def _run(de, monkeypatch, chunks, profile, payload=None):
    cap = {}
    monkeypatch.setattr(de, "_retrieve_chunks", lambda q: chunks)
    monkeypatch.setattr(de, "_load_profile", lambda: profile)
    monkeypatch.setattr(de.bedrock, "text_generate", lambda p, m: cap.setdefault("p", p) and "# t\n\nb")
    res = de.generate_draft(payload or {"title": "t"})
    return res, cap["p"]


@pytest.mark.parametrize("chunks,profile", [
    ([], {"tone": "x"}),
    (None, {"tone": "x"}),
    (["c"], {}),
    (["c"], {"tone": ""}),
    (["c"], None),
])
def test_general_style_when_profile_or_retrieval_empty(de, monkeypatch, chunks, profile):
    res, p = _run(de, monkeypatch, chunks, profile)
    assert res["generalStyle"] is True and res["topK"] == 0
    assert "일반적인 블로그 문체" in p
    assert "STYLE EXAMPLES" not in p and "Author style profile" not in p


def test_valid_style_is_not_general(de, monkeypatch):
    res, p = _run(de, monkeypatch, ["c1"], {"tone": "x"})
    assert res["generalStyle"] is False
    assert "일반적인 블로그 문체" not in p and "c1" in p


def test_fallback_keeps_voc_as_information_only(de, monkeypatch):
    voc = voc_store.create_voc("VOC-ONLY-BODY", "customer")
    res, p = _run(de, monkeypatch, [], {}, {"title": "t", "vocIds": [voc["vocId"]]})
    assert res["generalStyle"] is True
    assert "VOC-ONLY-BODY" in p[p.index("<<DATA: RESEARCH_CONTEXT"):]
    assert "STYLE_CONTEXT" not in p


def test_style_token_cap_limits_chunks():
    chunks = ["a" * 400] * 5  # 100 tokens each
    p = prompt_builder.build_prompt({}, chunks, "t", None, None, None, 6000, style_token_cap=250)
    assert p.count("a" * 400) == 2


def test_top_k_env(de, monkeypatch):
    monkeypatch.setenv("MABLOP_TOP_K", "3")
    assert de._top_k() == 3
    monkeypatch.setenv("MABLOP_TOP_K", "bad")
    assert de._top_k() == 8
