"""Draft_Index (users/{userId}/metadata/drafts.json): record, title derivation, back-fill, and the
`list-drafts` / `delete-draft` actions (Req 22; design D20, D21).

Every action has the signature ``fn(body, ctx, now=None)``: `now` is epoch seconds (default
``time.time()``). The actions run inside ``user_scope.bind`` (done by the handler), so
``s3store.user_key`` only ever yields the acting user's prefix (Req 22.13). Unexpected exceptions
(for example a failed object delete) propagate; the handler maps them to internal_error.
"""
import time

from botocore.exceptions import ClientError

import auth_crypto
import auth_messages
import s3store
from errors import MablopError

INDEX_REL = "metadata/drafts.json"
DRAFT_PREFIX = "generated-posts/"
DRAFT_SUFFIX = ".md"
LIST_CAP = 100
UNTITLED = auth_messages.DEFAULT_DRAFT_TITLE


def _empty_index() -> dict:
    return {"v": 1, "drafts": []}


def _now_ms(now) -> int:
    """Epoch milliseconds from epoch seconds (default: the current time)."""
    seconds = time.time() if now is None else now
    return int(seconds * 1000)


def derive_title(requested, markdown: str) -> str:
    """Request title if not blank, else the first '#' line of the body, else the default (Req 22.2)."""
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    for line in (markdown or "").splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip() or UNTITLED
    return UNTITLED


def valid_draft_id(draft_id) -> bool:
    """True for a non-empty string without '/', '\\', '..', or control characters (Req 22.9)."""
    if not isinstance(draft_id, str) or not draft_id:
        return False
    if "/" in draft_id or "\\" in draft_id or ".." in draft_id:
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in draft_id)


def _drafts_of(index) -> list:
    """The entry list of an index object; tolerates a missing or malformed index."""
    if isinstance(index, dict) and isinstance(index.get("drafts"), list):
        return [e for e in index["drafts"] if isinstance(e, dict) and isinstance(e.get("draftId"), str)]
    return []


def _with_drafts(index, drafts: list) -> dict:
    """Copy of the index object with its entry list replaced."""
    out = dict(index) if isinstance(index, dict) else {}
    out.setdefault("v", 1)
    out["drafts"] = drafts
    return out


def _object_key(draft_id: str) -> str:
    return s3store.user_key(f"{DRAFT_PREFIX}{draft_id}{DRAFT_SUFFIX}")


def record(draft_id: str, requested_title, markdown: str, now=None) -> None:
    """Add {draftId, title, createdAt} to the acting user's Draft_Index (Req 22.1, 22.3)."""
    entry = {
        "draftId": draft_id,
        "title": derive_title(requested_title, markdown),
        "createdAt": auth_crypto.to_iso_ms(_now_ms(now)),
    }

    def mutate(index):
        kept = [e for e in _drafts_of(index) if e["draftId"] != draft_id]
        kept.append(entry)
        return _with_drafts(index, kept)

    s3store.update_json(s3store.user_key(INDEX_REL), mutate, default=_empty_index())


def _stored_draft_objects() -> list[dict]:
    """[{draftId, lastModified}] for generated-posts/{id}.md objects of the acting user."""
    prefix = s3store.user_key(DRAFT_PREFIX)
    found = []
    for obj in s3store.list_keys(prefix):
        name = obj["key"][len(prefix):]
        if not name.endswith(DRAFT_SUFFIX):
            continue
        draft_id = name[: -len(DRAFT_SUFFIX)]
        if valid_draft_id(draft_id):
            found.append({"draftId": draft_id, "lastModified": obj["lastModified"]})
    return found


def _backfill_entries(known_ids: set) -> list[dict]:
    """Entries for stored objects that the index lacks; unreadable bodies are left for the next call."""
    entries = []
    for obj in _stored_draft_objects():
        if obj["draftId"] in known_ids:
            continue
        try:
            body = s3store.get_bytes(_object_key(obj["draftId"])).decode("utf-8", errors="replace")
        except ClientError:
            continue
        entries.append(
            {
                "draftId": obj["draftId"],
                "title": derive_title(None, body),
                "createdAt": obj["lastModified"],
            }
        )
    return entries


def list_drafts(body, ctx, now=None) -> dict:
    """Return the acting user's drafts, newest first, at most 100 (Req 22.5, 22.6, 22.7)."""
    found = s3store.read_json_optional(s3store.user_key(INDEX_REL))
    entries = _drafts_of(found[0]) if found is not None else []
    known = {e["draftId"] for e in entries}

    new_entries = _backfill_entries(known)
    if new_entries:

        def mutate(index):
            current = _drafts_of(index)
            present = {e["draftId"] for e in current}
            return _with_drafts(index, current + [e for e in new_entries if e["draftId"] not in present])

        written = s3store.update_json(s3store.user_key(INDEX_REL), mutate, default=_empty_index())
        entries = _drafts_of(written)

    ordered = sorted(
        entries,
        key=lambda e: (str(e.get("createdAt", "")), e["draftId"]),
        reverse=True,
    )[:LIST_CAP]
    return {
        "drafts": [
            {"draftId": e["draftId"], "title": e.get("title", UNTITLED), "createdAt": e.get("createdAt", "")}
            for e in ordered
        ]
    }


def delete_draft(body, ctx, now=None) -> dict:
    """Remove the index entry first, then the object (Req 22.8-22.11; design D21)."""
    draft_id = body.get("draftId") if isinstance(body, dict) else None
    if not valid_draft_id(draft_id):
        raise MablopError("bad_request", auth_messages.BAD_DRAFT_ID)

    index_key = s3store.user_key(INDEX_REL)
    object_key = _object_key(draft_id)
    found = s3store.read_json_optional(index_key)
    indexed = found is not None and any(e["draftId"] == draft_id for e in _drafts_of(found[0]))
    if not indexed and not s3store.key_exists(object_key):
        raise MablopError("not_found", auth_messages.DRAFT_NOT_FOUND)

    def mutate(index):
        return _with_drafts(index, [e for e in _drafts_of(index) if e["draftId"] != draft_id])

    s3store.update_json(index_key, mutate, default=_empty_index())
    provenance_key = s3store.user_key(f"{DRAFT_PREFIX}{draft_id}.provenance.json")
    if s3store.key_exists(provenance_key):  # research drafts only; legacy delete writes are unchanged
        s3store.delete(provenance_key)
    s3store.delete(object_key)  # a failure propagates; the handler maps it to internal_error
    return {"draftId": draft_id, "deleted": True}
