"""Copy-only migration of the pre-existing shared data to one user's prefix (Req 9.5-9.20, 8.10).

The Legacy_Migrator copies Legacy_Keys and Legacy_Drafts to the same relative path under
``users/{target}/``. It only reads the originals and only creates destination objects that are
absent. It imports no removal function, makes no write against a source key, and never replaces an
existing destination (D11). Learn checkpoints, ``images/*``, ``metadata/ratelimit.json`` and the
Draft_Index are not touched. Re-running is idempotent: equal-size destinations are skipped.
"""
import time

import auth_crypto
import auth_messages
import s3store

LEGACY_OWNER_LOGIN_ID = "jhe8888"  # Req 9.13
LEGACY_SINGLE_KEYS = (
    "embeddings/index.npy",
    "embeddings/index-map.json",
    "vector-index/manifest.json",
    "style-profile/profile.json",
    "metadata/learning-status.json",
)
LEGACY_LISTED_PREFIXES = ("raw-posts/", "generated-posts/")  # only ".txt" and ".md" keys respectively
_LISTED_SUFFIX = {"raw-posts/": ".txt", "generated-posts/": ".md"}
BINDING_KEY = "users/_auth/legacy-owner.json"  # Legacy_Binding_Record (Auth_Shared_Prefix)
LEARNING_STATUS_REL = "metadata/learning-status.json"
MIGRATION_REL = "metadata/migration.json"
NOT_APPLICABLE = {
    "status": "not_applicable",
    "copied": 0,
    "skipped": 0,
    "failedKeys": [],
    "divergedKeys": [],
    "message": None,
}


def _iso(seconds) -> str:
    """UTC ISO-8601 with milliseconds for an epoch-seconds value."""
    return auth_crypto.to_iso_ms(int(round(seconds * 1000)))


def _add(keys: list, key: str) -> None:
    """Append key once, keeping first-seen order."""
    if key not in keys:
        keys.append(key)


def _already_bound_result() -> dict:
    """Result for a run that stopped at the Legacy_Binding_Record (Req 9.17); nothing was copied."""
    return {
        "status": "already_bound",
        "copied": 0,
        "skipped": 0,
        "failedKeys": [],
        "divergedKeys": [],
        "message": auth_messages.LEGACY_ALREADY_BOUND,
    }


def _bind(target: str, created_at: str) -> bool:
    """Create the binding record for target; True when it names target afterwards (Req 9.16, D16)."""
    if s3store.put_json_if_absent(BINDING_KEY, {"userId": target, "createdAt": created_at}):
        return True
    record = s3store.get_json(BINDING_KEY)
    return isinstance(record, dict) and record.get("userId") == target


def _list_sizes(prefix: str, suffix: str | None, failed: list, label: str) -> dict | None:
    """{key: size} of the listed keys under prefix (optionally filtered by suffix); None on failure."""
    try:
        listed = s3store.list_keys(prefix)
    except Exception:
        _add(failed, label)
        return None
    return {obj["key"]: obj["size"] for obj in listed if suffix is None or obj["key"].endswith(suffix)}


def _list_exact(key: str) -> int | None:
    """Size of the exact key found by a list call, or None when absent (never a head request)."""
    for obj in s3store.list_keys(key):
        if obj["key"] == key:
            return obj["size"]
    return None


def _source_sizes(failed: list) -> tuple[dict, list]:
    """Sizes of the source objects by relative path, and the absent single keys (step 2)."""
    sources = {}
    absent = []
    for prefix in LEGACY_LISTED_PREFIXES:
        found = _list_sizes(prefix, _LISTED_SUFFIX[prefix], failed, prefix)
        for key, size in (found or {}).items():
            sources[key] = size
    for rel in LEGACY_SINGLE_KEYS:
        try:
            info = s3store.head(rel)  # None for 404/403
        except Exception:
            _add(failed, rel)
            continue
        if info is None:
            absent.append(rel)
        else:
            sources[rel] = info["size"]
    return sources, absent


def _destination_sizes(target: str, sources: dict, failed: list) -> dict:
    """Destination sizes by relative path, from list calls only (step 3).

    A relative path whose destination state could not be listed maps to None and is not copied.
    """
    user_prefix = f"users/{target}/"
    dest = {}
    groups = {prefix: None for prefix in LEGACY_LISTED_PREFIXES}
    for prefix in groups:
        found = _list_sizes(user_prefix + prefix, _LISTED_SUFFIX[prefix], failed, user_prefix + prefix)
        groups[prefix] = found
        if found is not None:
            for key, size in found.items():
                dest[key[len(user_prefix):]] = size
    for rel in sources:
        prefix = next((p for p in LEGACY_LISTED_PREFIXES if rel.startswith(p)), None)
        if prefix is not None:
            if groups[prefix] is None:
                dest[rel] = None  # unknown: do not copy
            continue
        try:
            size = _list_exact(user_prefix + rel)
        except Exception:
            _add(failed, rel)
            dest[rel] = None
            continue
        if size is not None:
            dest[rel] = size
    return dest


def _copy_all(target: str, sources: dict, dest: dict, failed: list, diverged: list):
    """Copy the sources whose destination is absent (step 4); return (copied_sizes, skipped)."""
    copied = {}
    skipped = 0
    for rel in sorted(sources):
        size = sources[rel]
        if rel in dest:
            existing = dest[rel]
            if existing is None:
                continue  # state unknown; already reported as failed
            if existing == size:
                skipped += 1
            else:
                _add(diverged, rel)
            continue
        try:
            s3store.copy(rel, s3store.user_key(rel, target))
        except Exception:
            _add(failed, rel)
            continue
        copied[rel] = size
    return copied, skipped


def _verify(target: str, sources: dict, copied: dict, diverged: list, failed: list):
    """Verification of step 5; return (rawPosts, generatedPosts, learningStatusEqual)."""
    user_prefix = f"users/{target}/"
    final = {}
    for prefix in LEGACY_LISTED_PREFIXES:
        found = _list_sizes(user_prefix + prefix, _LISTED_SUFFIX[prefix], failed, user_prefix + prefix)
        for key, size in (found or {}).items():
            final[key[len(user_prefix):]] = size
    for rel, size in copied.items():
        if rel in final:
            actual = final[rel]
        else:
            try:
                actual = _list_exact(user_prefix + rel)
            except Exception:
                actual = None
        if actual != size:
            _add(failed, rel)
    counts = {}
    for prefix in LEGACY_LISTED_PREFIXES:
        src_keys = [k for k in sources if k.startswith(prefix)]
        for rel in src_keys:
            if rel not in final and rel not in failed:
                _add(failed, rel)
        counts[prefix] = {
            "source": len(src_keys),
            "destination": sum(1 for k in src_keys if k in final),
        }
    status_equal = None
    if (
        LEARNING_STATUS_REL in sources
        and LEARNING_STATUS_REL not in diverged
        and LEARNING_STATUS_REL not in failed
    ):
        try:
            status_equal = s3store.get_json(LEARNING_STATUS_REL) == s3store.get_json(
                user_prefix + LEARNING_STATUS_REL
            )
        except Exception:
            status_equal = False
        if not status_equal:
            _add(failed, LEARNING_STATUS_REL)
    return counts["raw-posts/"], counts["generated-posts/"], status_equal


def run(target_user_id: str, now: float | None = None) -> dict:
    """Copy the legacy data to target_user_id and return the full result (Req 9.5-9.9, 9.16, 9.17).

    ``now`` is epoch seconds (default: current time). The result is also stored at
    ``users/{target}/metadata/migration.json``, except for ``already_bound`` (nothing is written).
    """
    started = time.time() if now is None else now
    s3store.user_key(MIGRATION_REL, target_user_id)  # rejects a malformed userId before any write
    if not _bind(target_user_id, _iso(started)):
        return _already_bound_result()

    failed: list = []
    diverged: list = []
    sources, absent = _source_sizes(failed)
    dest = _destination_sizes(target_user_id, sources, failed)
    copied, skipped = _copy_all(target_user_id, sources, dest, failed, diverged)
    raw_counts, draft_counts, status_equal = _verify(target_user_id, sources, copied, diverged, failed)

    result = {
        "status": "failed" if failed else "succeeded",
        "startedAt": _iso(started),
        "finishedAt": _iso(time.time() if now is None else now),
        "copied": len(copied),
        "skipped": skipped,
        "absent": absent,
        "rawPosts": raw_counts,
        "generatedPosts": draft_counts,
        "learningStatusEqual": status_equal,
        "failedKeys": failed,
        "divergedKeys": diverged,
    }
    try:
        s3store.put_json(s3store.user_key(MIGRATION_REL, target_user_id), result)
    except Exception:
        _add(failed, MIGRATION_REL)
        result["status"] = "failed"
    return result


def summarize(result: dict) -> dict:
    """The `legacyBinding` object of a run result (Req 9.11, 9.18)."""
    status = result.get("status") if isinstance(result, dict) else None
    if status == "succeeded":
        message = auth_messages.LEGACY_SUCCEEDED
    elif status == "already_bound":
        message = auth_messages.LEGACY_ALREADY_BOUND
    else:
        status = "failed"
        message = auth_messages.LEGACY_FAILED
    source = result if isinstance(result, dict) else {}
    return {
        "status": status,
        "copied": source.get("copied", 0),
        "skipped": source.get("skipped", 0),
        "failedKeys": list(source.get("failedKeys", [])),
        "divergedKeys": list(source.get("divergedKeys", [])),
        "message": message,
    }
