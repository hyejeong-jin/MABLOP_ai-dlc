"""Learn-run checkpoint/resume on S3 for the 15-min Lambda cap (Req 1.9, 1.10).

Stores accumulated chunks/embeddings at metadata/learn-checkpoint-<runId>.json so a
follow-up invocation resumes without recomputing done posts. Finalize hands off to
index_store (fixed keys => overwrite) and clears the checkpoint.
"""
import datetime
import os

import s3store
import index_store

TIME_BUDGET_ENV = "MABLOP_LEARN_TIME_BUDGET"
_DEFAULT_BUDGET_S = 840.0  # stay under the 900s Lambda cap


def checkpoint_key(run_id: str) -> str:
    """S3 key for a run's checkpoint."""
    return f"metadata/learn-checkpoint-{run_id}.json"


def _now() -> str:
    """UTC ISO timestamp."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def new_state(run_id: str) -> dict:
    """Fresh clean checkpoint state for a run."""
    ts = _now()
    return {
        "runId": run_id,
        "processedPosts": [],
        "chunks": [],
        "vectors": [],
        "startedAt": ts,
        "updatedAt": ts,
    }


def save(state: dict) -> str:
    """Persist state; stamp updatedAt. Return key."""
    state["updatedAt"] = _now()
    return s3store.put_json(checkpoint_key(state["runId"]), state)


def load(run_id: str):
    """Load prior state for run_id, or None if absent (fresh run)."""
    try:
        return s3store.get_json(checkpoint_key(run_id))
    except Exception:
        return None


def resume_or_new(run_id: str) -> dict:
    """Load existing checkpoint for run_id, else start clean."""
    return load(run_id) or new_state(run_id)


def is_processed(state: dict, post_id: str) -> bool:
    """True if post already done in this run."""
    return post_id in state["processedPosts"]


def record_post(state: dict, post_id: str, chunks, vectors) -> dict:
    """Append a post's chunks+vectors; mark done. Skip if already processed."""
    if is_processed(state, post_id):
        return state
    state["processedPosts"].append(post_id)
    state["chunks"].extend(chunks)
    state["vectors"].extend(vectors)
    return state


def _budget() -> float:
    """Time budget seconds from env, else default."""
    try:
        return float(os.environ.get(TIME_BUDGET_ENV, _DEFAULT_BUDGET_S))
    except ValueError:
        return _DEFAULT_BUDGET_S


def should_pause(elapsed_s: float) -> bool:
    """True when elapsed meets/exceeds the budget => pause and resume later."""
    return elapsed_s >= _budget()


def finalize(state: dict):
    """All posts done: build+store index from accumulated vectors+chunks, clear checkpoint.

    Fixed index keys => re-learn overwrites (Req 1.10). Returns the index manifest.
    """
    manifest = index_store.build_and_store_index(state["vectors"], state["chunks"])
    clear(state["runId"])
    return manifest


def clear(run_id: str) -> None:
    """Delete the checkpoint; ignore if already gone."""
    try:
        s3store._client().delete_object(Bucket=s3store._bucket(), Key=checkpoint_key(run_id))
    except Exception:
        pass
