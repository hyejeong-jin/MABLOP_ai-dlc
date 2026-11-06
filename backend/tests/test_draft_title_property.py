"""Property test: draft title derivation (Req 22.2; Property 34).

``derive_title`` is compared with an independent oracle written in this file. ``record`` is
checked on two examples under moto to confirm it stores the same title in the Draft_Index.
Production code is not modified.

Feature: user-auth-admin, Property 34: Draft title derivation
"""
from hypothesis import given, settings
from hypothesis import strategies as st

import draft_history
import s3store

DEFAULT_TITLE = "제목 없음"  # literal on purpose: not imported from production code

_SETTINGS = settings(max_examples=200, deadline=None)

# Characters that str.splitlines() treats as line boundaries. They are kept out of line content
# so the generated body has exactly the lines the oracle is given.
_BOUNDARIES = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
_line_chars = st.characters(blacklist_characters=_BOUNDARIES, blacklist_categories=("Cs",))
_plain_text = st.text(_line_chars, max_size=25)
_hash_prefix = st.text(alphabet="# \t", max_size=6)


@st.composite
def _lines(draw):
    """One body line: a '#' line (maybe '#'-only), or a line that does not start with '#'."""
    kind = draw(st.sampled_from(["hash", "hash_only", "plain", "indented_hash", "blank"]))
    text = draw(_plain_text)
    if kind == "hash":
        return "#" + draw(_hash_prefix) + text
    if kind == "hash_only":
        return "#" + draw(st.text(alphabet="# ", max_size=5))
    if kind == "indented_hash":
        return draw(st.sampled_from([" ", "\t", "  "])) + "#" + text
    if kind == "blank":
        return draw(st.text(alphabet=" \t", max_size=3))
    return draw(st.sampled_from(["a", "x ", "-"])) + text


_bodies = st.tuples(
    st.lists(_lines(), max_size=6),
    st.lists(st.sampled_from(["\n", "\r\n", "\r"]), min_size=6, max_size=6),
    st.booleans(),
)

# Request titles: absent, non-string, blank, and text containing newlines and control characters.
_blank = st.text(alphabet=" \t\n\r\x0b\x0c\x1c\u3000\u2028", max_size=6)
_noisy = st.text(
    st.characters(blacklist_categories=("Cs",)),
    max_size=30,
)
_requested = st.one_of(
    st.none(),
    st.integers(),
    st.booleans(),
    st.binary(max_size=8),
    st.lists(st.text(max_size=3), max_size=3),
    _blank,
    _noisy,
    st.tuples(_blank, _noisy, _blank).map("".join),
)


def _build_markdown(lines, seps, trailing):
    out = ""
    for i, line in enumerate(lines):
        out += line
        if i < len(lines) - 1 or trailing:
            out += seps[i % len(seps)]
    return out


def _trim(text):
    start, end = 0, len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return text[start:end]


def _oracle(requested, lines):
    """Independent statement of Req 22.2."""
    if isinstance(requested, str):
        trimmed = _trim(requested)
        if trimmed:
            return trimmed
    for line in lines:
        if line[:1] == "#":
            pos = 0
            while pos < len(line) and line[pos] in "# ":
                pos += 1
            return _trim(line[pos:]) or DEFAULT_TITLE
    return DEFAULT_TITLE


@_SETTINGS
@given(requested=_requested, body=_bodies)
def test_derive_title_matches_oracle(requested, body):
    """Validates: Requirements 22.2"""
    lines, seps, trailing = body
    markdown = _build_markdown(lines, seps, trailing)
    assert draft_history.derive_title(requested, markdown) == _oracle(requested, lines)


@_SETTINGS
@given(requested=_noisy, body=_bodies)
def test_non_blank_request_title_wins_over_body(requested, body):
    """Validates: Requirements 22.2"""
    lines, seps, trailing = body
    markdown = _build_markdown(lines, seps, trailing)
    result = draft_history.derive_title(requested, markdown)
    if _trim(requested):
        assert result == _trim(requested)
    else:
        assert result == _oracle(None, lines)


@_SETTINGS
@given(
    first=st.text(_line_chars, min_size=1, max_size=15).filter(lambda t: _trim(t) and not t.startswith("#")),
    second=st.text(_line_chars, min_size=1, max_size=15).filter(lambda t: _trim(t)),
)
def test_only_first_hash_line_is_used(first, second):
    """Validates: Requirements 22.2"""
    markdown = f"intro\n# {first}\n## {second}\n"
    assert draft_history.derive_title(None, markdown) == _oracle(None, ["intro", "# " + first, "## " + second])


def test_fixed_examples():
    """Validates: Requirements 22.2"""
    derive = draft_history.derive_title
    assert derive("  제목 \n", "# 본문") == "제목"
    assert derive("a\nb", "") == "a\nb"
    assert derive("   ", "intro\n#  ## 본문 제목  \n# 둘째") == "본문 제목"
    assert derive(None, "intro\n본문\n") == DEFAULT_TITLE
    assert derive(None, "#\n# 둘째") == DEFAULT_TITLE  # '#'-only first line: default, not the next line
    assert derive(None, "  # 들여쓰기\n") == DEFAULT_TITLE  # leading whitespace is not skipped
    assert derive(123, "# 본문") == "본문"
    assert derive(None, "") == DEFAULT_TITLE
    assert derive("", None) == DEFAULT_TITLE


def _stored_title(draft_id):
    found = s3store.read_json_optional(s3store.user_key("metadata/drafts.json"))
    entries = [e for e in found[0]["drafts"] if e["draftId"] == draft_id]
    assert len(entries) == 1
    return entries[0]["title"]


def test_record_stores_request_title_when_not_blank(s3_bucket):
    """Validates: Requirements 22.2"""
    draft_history.record("draft-a", "  요청 제목  ", "# 본문 제목\n내용", now=1_700_000_000)
    assert _stored_title("draft-a") == "요청 제목"


def test_record_falls_back_to_body_then_default(s3_bucket):
    """Validates: Requirements 22.2"""
    draft_history.record("draft-b", None, "소개\n## 본문 제목 \n# 다음", now=1_700_000_000)
    draft_history.record("draft-c", "   ", "제목 줄 없음", now=1_700_000_001)
    assert _stored_title("draft-b") == "본문 제목"
    assert _stored_title("draft-c") == DEFAULT_TITLE
