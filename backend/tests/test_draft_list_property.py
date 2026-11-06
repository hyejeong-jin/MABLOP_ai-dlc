"""Property test: list-drafts is ordered and capped (Req 22.7; Property 36).

The Draft_Index is written straight into moto S3 (users/{id}/metadata/drafts.json) and mixed with
generated-posts objects the index lacks, objects of another user, and non-.md objects. The result of
draft_history.list_drafts is compared with a reference model. Production code is not modified.

Feature: user-auth-admin, Property 36: list-drafts is ordered and capped
"""
import json

import boto3
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_messages
import draft_history
import s3store
from conftest import ACTING_USER_ID, OTHER_USER_ID, TEST_BUCKET, TEST_REGION

CAP = 100
ENTRY_FIELDS = {"draftId", "title", "createdAt"}

_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# Small timestamp pools make createdAt ties common. The "late" pool lies after any back-filled
# lastModified (the moto clock), the "early" pool before it, so both orderings are exercised.
_EARLY_BASE_MS = 1_700_000_000_000
_LATE_BASE_MS = 4_100_000_000_000
_created_ms = st.one_of(
    st.integers(min_value=0, max_value=6).map(lambda n: _EARLY_BASE_MS + n * 1000),
    st.integers(min_value=0, max_value=6).map(lambda n: _LATE_BASE_MS + n * 1000),
)

_id_suffix = st.text(alphabet="abcXYZ019-_", min_size=1, max_size=6)
_title = st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=12)

# (draftId, title, created ms, has_object, has_extra_field); index ids start with "d".
_indexed = st.lists(
    st.tuples(
        _id_suffix.map(lambda s: "d" + s),
        _title,
        _created_ms,
        st.booleans(),
        st.booleans(),
    ),
    min_size=0,
    max_size=130,
    unique_by=lambda t: t[0],
)
# (title or None) per object missing from the index; ids are "o0", "o1", ...
_unindexed = st.lists(st.one_of(st.none(), _title), min_size=0, max_size=4)
_noise = st.booleans()


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _gen_key(draft_id: str, user_id: str = ACTING_USER_ID) -> str:
    return f"users/{user_id}/generated-posts/{draft_id}.md"


@_SETTINGS
@example(  # always exercise the cap: 130 entries with heavy createdAt ties, plus extras and noise
    indexed=[
        (f"d{n:03d}", f"t{n}", _EARLY_BASE_MS + (n % 5) * 1000 if n % 2 else _LATE_BASE_MS + (n % 5) * 1000, n % 7 == 0, n % 3 == 0)
        for n in range(130)
    ],
    unindexed=["x", None],
    noise=True,
)
@given(indexed=_indexed, unindexed=_unindexed, noise=_noise)
def test_list_drafts_is_ordered_capped_and_exact(s3_bucket, indexed, unindexed, noise):
    """Validates: Requirements 22.7"""
    _wipe_bucket()

    # --- arrange: the acting user's index and objects -----------------------------------
    index_entries = []
    for draft_id, title, ms, has_object, has_extra in indexed:
        entry = {"draftId": draft_id, "title": title, "createdAt": auth_crypto.to_iso_ms(ms)}
        if has_extra:
            entry["note"] = "must not be returned"
        index_entries.append(entry)
        if has_object:
            s3store.put_bytes(_gen_key(draft_id), f"# {title}\n".encode("utf-8"))
    if index_entries:
        s3store.put_json(
            f"users/{ACTING_USER_ID}/metadata/drafts.json", {"v": 1, "drafts": index_entries}
        )

    unindexed_titles = {}
    for n, title in enumerate(unindexed):
        draft_id = f"o{n}"
        body = "plain body without heading" if title is None else f"intro\n# {title}\nmore"
        s3store.put_bytes(_gen_key(draft_id), body.encode("utf-8"))
        unindexed_titles[draft_id] = auth_messages.DEFAULT_DRAFT_TITLE if title is None else title

    # --- arrange: things that must be ignored -------------------------------------------
    if noise:
        s3store.put_bytes(_gen_key("zz-other", OTHER_USER_ID), b"# other user\n")
        s3store.put_json(
            f"users/{OTHER_USER_ID}/metadata/drafts.json",
            {
                "v": 1,
                "drafts": [
                    {
                        "draftId": "zz-other-indexed",
                        "title": "other",
                        "createdAt": auth_crypto.to_iso_ms(_LATE_BASE_MS + 999_000),
                    }
                ],
            },
        )
        prefix = f"users/{ACTING_USER_ID}/generated-posts/"
        s3store.put_bytes(prefix + "zz-note.txt", b"# not a draft\n")
        s3store.put_bytes(prefix + "zz-backup.md.bak", b"# not a draft\n")
        s3store.put_bytes(prefix + "sub/zz-nested.md", b"# nested\n")

    # lastModified of the objects the index lacks (the back-fill createdAt, Req 22.6)
    modified = {
        obj["key"]: obj["lastModified"]
        for obj in s3store.list_keys(f"users/{ACTING_USER_ID}/generated-posts/")
    }

    # --- reference model ----------------------------------------------------------------
    model = {
        e["draftId"]: {"draftId": e["draftId"], "title": e["title"], "createdAt": e["createdAt"]}
        for e in index_entries
    }
    for draft_id, title in unindexed_titles.items():
        model[draft_id] = {
            "draftId": draft_id,
            "title": title,
            "createdAt": modified[_gen_key(draft_id)],
        }
    expected = sorted(model.values(), key=lambda e: (e["createdAt"], e["draftId"]), reverse=True)[:CAP]

    # --- act ----------------------------------------------------------------------------
    result = draft_history.list_drafts({}, None)
    drafts = result["drafts"]

    # --- assert -------------------------------------------------------------------------
    assert set(result) == {"drafts"}
    assert len(drafts) <= CAP
    assert len(drafts) == min(CAP, len(model))
    for item in drafts:
        assert set(item) == ENTRY_FIELDS  # (c) exactly these fields
    ids = [item["draftId"] for item in drafts]
    assert len(ids) == len(set(ids))  # (d) no duplicates
    assert not any(i.startswith("zz-") for i in ids)  # (e) other user / non-.md / nested ignored
    keys = [(item["createdAt"], item["draftId"]) for item in drafts]
    assert keys == sorted(keys, reverse=True)  # (a) createdAt desc, ties by draftId desc
    assert drafts == expected  # (b) the newest 100, equal to the model, title included
    json.dumps(result)  # response is JSON-serializable
