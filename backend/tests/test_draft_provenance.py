"""Task 7.3: provenance save/get contract for generate-draft / get-result / delete-draft.
Requirements 6.3, 9.4, 13.4. moto S3 backs storage; Bedrock is monkeypatched.
"""
import importlib

import pytest

import draft_history
import research_store
import s3store
import voc_store
from test_drafting_research_context import (
    RUN_ID, SRC_ID, _patch, _seed_research, de,  # noqa: F401 - de is a fixture
)


def _prov_key(draft_id):
    return s3store.user_key("generated-posts/" + draft_id + ".provenance.json")


def test_provenance_saved_returned_and_labelled(de, monkeypatch):  # noqa: F811
    _seed_research()
    voc = voc_store.create_voc("other", "t", "VOC-TEXT")
    _patch(de, monkeypatch, [])
    result = de.generate_draft({"title": "t", "researchRunId": RUN_ID, "vocIds": [voc["vocId"]]})

    prov = result["provenance"]
    assert result["researchRunId"] == RUN_ID
    assert prov["draftId"] == result["draftId"]
    assert prov["researchRunId"] == RUN_ID and prov["researchStatus"] == "completed"
    assert prov["vocIds"] == [voc["vocId"]] and prov["generalStyle"] is True
    assert [s["sourceId"] for s in prov["sources"]] == [SRC_ID]
    labels = {s["label"] for s in prov["segments"]}
    assert {"fact", "user-input", "generated"} <= labels
    fact = next(s for s in prov["segments"] if s["label"] == "fact")
    assert fact["sourceIds"] == [SRC_ID]
    assert s3store.key_exists(_prov_key(result["draftId"]))

    got = de.get_result({"id": result["draftId"]})
    assert got["provenance"] == prov and got["markdown"] == result["markdown"]


def test_fact_without_existing_source_is_unverified(de):  # noqa: F811
    ctx = {"summary": {"facts": [{"text": "x", "sourceIds": ["src_missing"]}]},
           "sources": [], "voc": [], "runId": None, "runStatus": None}
    prov = de.build_provenance("d", "body", ctx, False)
    assert [s["label"] for s in prov["segments"]] == ["unverified", "generated"]


def test_legacy_draft_has_no_provenance(de, monkeypatch):  # noqa: F811
    _patch(de, monkeypatch, [])
    result = de.generate_draft({"title": "t"})
    assert "provenance" not in result and "researchRunId" not in result
    assert not s3store.key_exists(_prov_key(result["draftId"]))
    assert set(de.get_result({"id": result["draftId"]})) == {"status", "markdown"}


def test_delete_draft_removes_provenance(de, monkeypatch):  # noqa: F811
    _seed_research()
    _patch(de, monkeypatch, [])
    result = de.generate_draft({"title": "t", "researchRunId": RUN_ID})
    assert s3store.key_exists(_prov_key(result["draftId"]))
    draft_history.delete_draft({"draftId": result["draftId"]}, {})
    assert not s3store.key_exists(_prov_key(result["draftId"]))
