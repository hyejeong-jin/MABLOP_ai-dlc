"""Property-based test for the prompt builder token cap.

Feature: mablop-mvp, Property 7
Validates: Requirements 2.6, 7.1
Design: Property 7 (prompt stays within the context token cap).
"""
from hypothesis import given, settings
from hypothesis import strategies as st

import prompt_builder as pb

# Mix ASCII, unicode, and Korean so the heuristic is exercised across scripts.
_text = st.text(
    alphabet=st.one_of(
        st.characters(min_codepoint=0x20, max_codepoint=0x7E),  # ASCII
        st.sampled_from("가나다라마바사아자차카타파하힣원영글자"),  # Korean
        st.sampled_from("—…€✓«»あ漢"),                             # misc unicode
    ),
    min_size=0,
    max_size=4000,  # single-field sizes incl. "huge" relative to small caps
)

_chunks = st.lists(_text, min_size=0, max_size=12)

_profile = st.one_of(
    st.none(),
    st.dictionaries(
        st.sampled_from(["tone", "sentence_length", "expressions", "구조"]),
        _text,
        max_size=4,
    ),
    _text,  # profile may also arrive as a plain string
)

_captions = st.one_of(
    st.none(),
    st.lists(_text, max_size=10),
    st.dictionaries(st.integers(min_value=1, max_value=10), _text, max_size=10),
)


@settings(max_examples=100, deadline=None)
@given(
    style_profile=_profile,
    chunks=_chunks,
    title=_text,
    outline=_text,
    notes=_text,
    captions=_captions,
    token_cap=st.integers(min_value=1, max_value=4000),
)
def test_prompt_within_token_cap(
    style_profile, chunks, title, outline, notes, captions, token_cap
):
    """Feature: mablop-mvp, Property 7

    Validates: Requirements 2.6, 7.1
    """
    prompt = pb.build_prompt(
        style_profile, chunks, title, outline, notes, captions,
        token_cap=token_cap,
    )
    assert pb.estimate_tokens(prompt) <= token_cap
