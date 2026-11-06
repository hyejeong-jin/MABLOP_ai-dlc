"""Property test: image keys must belong to the acting user (Req 8.7; Property 16).

For any image key in an analyze-images or generate-draft request, the request proceeds if and
only if the key is an image key under the acting user's prefix. Otherwise it fails with
bad_request "허용되지 않는 이미지 경로입니다." before any object read, Bedrock call, or write.

moto backs S3 and the ``s3_calls`` recorder shows which S3 calls happen. The Bedrock calls and
the retrieval step are stubbed, so no live AWS or model call is made. An independent oracle
(_own_image_key) restates the rule from design.md and is compared against the real code.

Feature: user-auth-admin, Property 16: Image keys must belong to the acting user
"""
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_messages
import bedrock
import drafting_engine
import image_analyzer
import s3store
import user_scope
from errors import MablopError

BAD_IMAGE_MSG = "허용되지 않는 이미지 경로입니다."  # literal from Req 8.7 (design D14)


# --- oracle ------------------------------------------------------------------------------

def _own_image_key(key, user_id) -> bool:
    """Oracle: starts with users/{id}/images/, has a name after it, no '..', backslash or control char."""
    prefix = f"users/{user_id}/images/"
    if not isinstance(key, str) or not key.startswith(prefix) or len(key) <= len(prefix):
        return False
    if ".." in key or "\\" in key:
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in key)


# --- strategies --------------------------------------------------------------------------

_ids = st.text(alphabet="0123456789abcdef", min_size=16, max_size=16).map(lambda h: "u_" + h)
_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-. 한글/", min_size=1, max_size=20).filter(
    lambda n: ".." not in n
)
_fragments = st.sampled_from(["a", "b/", "..", ".", "/", "\\", "\x00", "\n", "\x7f", "한글", "x.png", "a..b"])


@st.composite
def _key_cases(draw, user_id):
    """One image key (valid, near-miss, or non-string) for the acting user."""
    other = draw(_ids.filter(lambda i: i != user_id))
    name = draw(_names)
    kind = draw(
        st.sampled_from(
            [
                "own", "own", "own",
                "foreign", "wrong_dir", "no_slash", "empty_name", "traversal", "traversal_out",
                "backslash", "control", "no_users", "leading_slash", "auth_area", "mixed", "non_str",
            ]
        )
    )
    if kind == "own":
        return f"users/{user_id}/images/{name}"
    if kind == "foreign":
        return f"users/{other}/images/{name}"
    if kind == "wrong_dir":
        sub = draw(st.sampled_from(["generated-posts", "raw-posts", "style-profile", "images2", "Images", "index"]))
        return f"users/{user_id}/{sub}/{name}"
    if kind == "no_slash":
        return f"users/{user_id}/images"
    if kind == "empty_name":
        return f"users/{user_id}/images/"
    if kind == "traversal":
        return f"users/{user_id}/images/{draw(_names)}..{draw(_names)}"
    if kind == "traversal_out":
        return f"users/{user_id}/images/../{other}/images/{name}"
    if kind == "backslash":
        return f"users/{user_id}/images/{draw(_names)}\\{draw(_names)}"
    if kind == "control":
        ctrl = draw(st.sampled_from(["\x00", "\n", "\x1f", "\x7f"]))
        return f"users/{user_id}/images/{draw(_names)}{ctrl}x"
    if kind == "no_users":
        return f"images/{name}"
    if kind == "leading_slash":
        return f"/users/{user_id}/images/{name}"
    if kind == "auth_area":
        return f"users/_auth/images/{name}"
    if kind == "mixed":
        return f"users/{user_id}/images/" + "".join(draw(st.lists(_fragments, max_size=4)))
    return draw(st.sampled_from([None, 0, 123, b"x", ["a"], {"k": "v"}, True]))


@st.composite
def _scenario(draw, max_keys=5):
    """(acting user id, list of key cases). Own keys are likely enough that both outcomes occur often."""
    user_id = draw(_ids)
    keys = draw(st.lists(_key_cases(user_id), max_size=max_keys))
    return user_id, keys


_SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --- fixtures ----------------------------------------------------------------------------

@pytest.fixture
def stubs(s3_bucket, s3_calls, monkeypatch):
    """moto bucket + S3 call recorder; Bedrock and retrieval are stubbed and counted."""
    state = {"bedrock": 0, "retrieve": 0}

    def _vision(*_a, **_k):
        state["bedrock"] += 1
        return "비전 캡션"

    def _text(*_a, **_k):
        state["bedrock"] += 1
        return "# 제목\n\n본문"

    def _embed(*_a, **_k):
        state["bedrock"] += 1
        return [1.0, 0.0, 0.0]

    def _retrieve(_text_in):
        state["retrieve"] += 1
        return []

    monkeypatch.setattr(image_analyzer, "_vision_caption", _vision)
    monkeypatch.setattr(bedrock, "text_generate", _text)
    monkeypatch.setattr(bedrock, "embed", _embed)
    monkeypatch.setattr(drafting_engine, "_retrieve_chunks", _retrieve)
    s3_calls.state = state

    def reset():
        s3_calls.clear()
        state["bedrock"] = 0
        state["retrieve"] = 0

    s3_calls.reset = reset
    return s3_calls


def _assert_rejected(info):
    assert info.value.code == "bad_request"
    assert info.value.message == BAD_IMAGE_MSG == auth_messages.BAD_IMAGE_PATH


def _assert_nothing_happened(rec):
    """No object read or write, no Bedrock call, no retrieval."""
    assert rec.calls == []
    assert rec.state == {"bedrock": 0, "retrieve": 0}


# --- s3store.assert_own_image_keys -------------------------------------------------------

@settings(max_examples=200, deadline=None)
@given(case=_scenario(max_keys=6))
def test_assert_own_image_keys_accepts_iff_all_keys_are_own(case):
    """Validates: Requirements 8.7"""
    user_id, keys = case
    with user_scope.bind(user_id):
        if all(_own_image_key(k, user_id) for k in keys):
            s3store.assert_own_image_keys(keys)
        else:
            with pytest.raises(MablopError) as info:
                s3store.assert_own_image_keys(keys)
            _assert_rejected(info)


# --- image_analyzer.analyze_images -------------------------------------------------------

@_SETTINGS
@given(case=_scenario(), descriptions=st.lists(st.one_of(st.none(), st.just("사용자 설명")), min_size=5, max_size=5))
def test_analyze_images_proceeds_iff_all_keys_are_own(stubs, case, descriptions):
    """Validates: Requirements 8.7"""
    user_id, keys = case
    images = [{"key": k, "description": descriptions[i]} for i, k in enumerate(keys)]
    stubs.reset()
    with user_scope.bind(user_id):
        if all(_own_image_key(k, user_id) for k in keys):
            result = image_analyzer.analyze_images(images)
            assert [c["key"] for c in result["captions"]] == keys
            # every object touched stays inside the acting user's prefix
            assert all(k.startswith(f"users/{user_id}/") for k in stubs.touched_keys())
        else:
            with pytest.raises(MablopError) as info:
                image_analyzer.analyze_images(images)
            _assert_rejected(info)
            _assert_nothing_happened(stubs)


def test_analyze_images_rejects_items_without_a_string_key(stubs):
    """Validates: Requirements 8.7"""
    own = f"users/{'u_0123456789abcdef'}/images/a.png"
    for bad in ([{}], [None], ["users/u_0123456789abcdef/images/a.png"], [{"key": own}, {"description": "x"}]):
        stubs.reset()
        with user_scope.bind("u_0123456789abcdef"):
            with pytest.raises(MablopError) as info:
                image_analyzer.analyze_images(bad)
        _assert_rejected(info)
        _assert_nothing_happened(stubs)


# --- drafting_engine.generate_draft ------------------------------------------------------

@_SETTINGS
@given(case=_scenario(max_keys=12))
def test_generate_draft_proceeds_iff_all_keys_are_own(stubs, case):
    """Validates: Requirements 8.7"""
    user_id, keys = case
    payload = {"title": "제목", "outline": "개요", "notes": "메모", "imageKeys": keys}
    stubs.reset()
    with user_scope.bind(user_id):
        if all(_own_image_key(k, user_id) for k in keys):
            result = drafting_engine.generate_draft(payload)
            assert result["draftKey"].startswith(f"users/{user_id}/generated-posts/")
            assert all(k.startswith(f"users/{user_id}/") for k in stubs.touched_keys())
        else:
            with pytest.raises(MablopError) as info:
                drafting_engine.generate_draft(payload)
            _assert_rejected(info)
            _assert_nothing_happened(stubs)


def test_generate_draft_checks_keys_beyond_the_ten_image_cap(stubs):
    """Validates: Requirements 8.7"""
    uid = "u_0123456789abcdef"
    keys = [f"users/{uid}/images/{i}.png" for i in range(10)] + [f"users/u_fedcba9876543210/images/x.png"]
    with user_scope.bind(uid):
        with pytest.raises(MablopError) as info:
            drafting_engine.generate_draft({"title": "t", "imageKeys": keys})
    _assert_rejected(info)
    _assert_nothing_happened(stubs)
