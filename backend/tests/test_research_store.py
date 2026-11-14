"""Minimal storage coverage for research sources, runs, summaries, and cache."""
import pytest

import research_store
import user_scope
from errors import MablopError


def source(source_id="src_" + "a" * 32, **changes):
    value = {
        "sourceId": source_id,
        "runId": "run_" + "b" * 32,
        "sourceType": "primary-link",
        "title": "자료",
        "domain": "example.com",
        "url": "https://example.com/a#fragment",
        "normalizedUrl": "https://example.com/a",
        "collectedAt": "2025-01-01T00:00:00.000Z",
        "status": "succeeded",
        "errorCode": None,
        "errorMessage": None,
        "excerpt": "발췌",
        "sourceLabel": "fact",
    }
    value.update(changes)
    return value


def test_source_failure_is_preserved_and_listed(s3_bucket):
    failed = research_store.save_info_source(
        source(status="failed", errorCode="timeout", errorMessage="수집 시간 초과", excerpt="")
    )
    assert failed["status"] == "failed"
    assert failed["errorCode"] == "timeout"
    assert research_store.list_info_sources()[0]["errorMessage"] == "수집 시간 초과"


def test_run_summary_and_cache_use_user_prefix_and_update_index(s3_bucket, acting_user_id):
    run_id = "run_" + "c" * 32
    summary_id = "summary_" + "d" * 32
    run = research_store.save_research_run({
        "runId": run_id,
        "status": "partial",
        "summaryId": summary_id,
        "requestedAt": "2025-01-01T00:00:00.000Z",
        "completedAt": None,
    })
    summary = research_store.save_research_summary({"summaryId": summary_id, "runId": run_id})
    cache = research_store.save_cache("sha256:" + "e" * 64, {"expiresAt": "2025-01-02T00:00:00.000Z"})

    assert research_store.get_research_run(run_id) == run
    assert research_store.get_research_summary(summary_id) == summary
    assert research_store.get_cache(cache["cacheKey"]) == cache
    assert research_store.list_research_runs()["runs"][0]["runId"] == run_id
    assert all(item["key"].startswith(f"users/{acting_user_id}/") for item in __import__("s3store").list_keys(""))


def test_other_user_cannot_read_or_list_sources(s3_bucket, other_user_id):
    saved = research_store.save_info_source(source())
    with user_scope.bind(other_user_id):
        assert research_store.list_info_sources() == []
        with pytest.raises(MablopError) as error:
            research_store.get_info_source(saved["sourceId"])
    assert error.value.code == "not_found"


def test_research_usage_is_recorded_alongside_model_usage(s3_bucket):
    import usage_meter

    usage_meter.record_research({"requests": 3, "searchCalls": 1}, False, now=1_750_000_000)
    usage_meter.record_research({}, True, now=1_750_000_000)
    got = usage_meter.get_usage({}, None, now=1_750_000_000)
    assert got["research"]["runs"] == 2
    assert got["research"]["cacheHits"] == 1 and got["research"]["cacheMisses"] == 1
    assert got["research"]["requests"] == 3 and got["research"]["searchCalls"] == 1
    assert got["models"] == [] and "activity" in got
