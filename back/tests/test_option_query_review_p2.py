from __future__ import annotations

import pytest

from dano.execution.page.option_query_review_p2 import (
    apply_option_review_decisions,
    prepare_reviewable_selects,
    public_selects,
    public_transaction_ir,
    trusted_identity,
)


def inferred_select() -> dict:
    return {
        "path": "approverId",
        "label": "张经理",
        "count": 20,
        "source_url": "https://oa.example/api/users/search",
        "source_method": "POST",
        "source_post_data": {"keyword": "张"},
        "source_headers": {"X-Tenant": "T1"},
        "source_records_path": ["data", "rows"],
        "value_key": "id",
        "label_key": "name",
        "option_query": {
            "search": {"location": "json", "path": ["keyword"]},
            "pagination": {"mode": "page", "location": "json", "path": ["pageNo"]},
            "dependencies": [{
                "field": "部门",
                "field_path": "departmentId",
                "location": "json",
                "path": ["departmentId"],
            }],
        },
        "option_query_inference": {
            "status": "inferred",
            "confidence": 0.94,
            "confirmed_by_user": False,
            "source_fingerprint": "fp-source",
            "evidence": [{
                "kind": "search",
                "evidence_refs": ["ui:1", "read:2"],
                "reason": "matched",
            }],
        },
    }


def test_public_projection_hides_source_implementation() -> None:
    server = prepare_reviewable_selects([inferred_select()])
    public = public_selects(server)

    assert len(public) == 1
    item = public[0]
    assert item["path"] == "approverId"
    assert item["capabilities"] == {
        "search": True,
        "pagination": "page",
        "validation": False,
        "dependencies": ["部门"],
    }
    assert item["inference"]["review_id"].startswith("oqr_")
    assert item["inference"]["evidence_count"] == 2
    dumped = repr(public)
    for secret in (
        "source_url", "source_method", "source_post_data", "source_headers",
        "source_records_path", "value_key", "label_key", "keyword", "pageNo",
        "departmentId", "oa.example",
    ):
        assert secret not in dumped


def test_accept_marks_server_protocol_confirmed() -> None:
    server = prepare_reviewable_selects([inferred_select()])
    review_id = public_selects(server)[0]["inference"]["review_id"]

    accepted = apply_option_review_decisions(server, [{
        "review_id": review_id,
        "decision": "accept",
        "option_query": {"search": {"path": ["evil"]}},
    }])

    assert accepted[0]["option_query"]["search"]["path"] == ["keyword"]
    assert accepted[0]["option_query_inference"]["status"] == "confirmed"
    assert accepted[0]["option_query_inference"]["confirmed_by_user"] is True
    assert accepted[0]["option_query_inference"]["review_id"] == review_id
    assert "_option_review_id" not in accepted[0]


def test_reject_removes_only_inferred_query_protocol() -> None:
    server = prepare_reviewable_selects([inferred_select()])
    review_id = public_selects(server)[0]["inference"]["review_id"]

    rejected = apply_option_review_decisions(server, [{
        "review_id": review_id,
        "decision": "reject",
    }])

    assert "option_query" not in rejected[0]
    assert "option_query_inference" not in rejected[0]
    assert rejected[0]["source_url"].endswith("/users/search")
    assert rejected[0]["value_key"] == "id"


def test_all_pending_reviews_must_be_resolved() -> None:
    first = inferred_select()
    second = inferred_select()
    second["path"] = "roomId"
    second["label"] = "一号会议室"
    server = prepare_reviewable_selects([first, second])

    with pytest.raises(ValueError, match="还有 2 条"):
        apply_option_review_decisions(server, [])


def test_unknown_duplicate_and_invalid_decisions_fail_closed() -> None:
    server = prepare_reviewable_selects([inferred_select()])
    review_id = public_selects(server)[0]["inference"]["review_id"]

    with pytest.raises(ValueError, match="未知或已过期"):
        apply_option_review_decisions(server, [{"review_id": "oqr_unknown", "decision": "accept"}])
    with pytest.raises(ValueError, match="重复"):
        apply_option_review_decisions(server, [
            {"review_id": review_id, "decision": "accept"},
            {"review_id": review_id, "decision": "reject"},
        ])
    with pytest.raises(ValueError, match="accept/reject"):
        apply_option_review_decisions(server, [{"review_id": review_id, "decision": "edit"}])


def test_authored_protocol_needs_no_review() -> None:
    select = inferred_select()
    select["option_query_inference"] = {
        "status": "authored",
        "confirmed_by_user": True,
    }

    prepared = prepare_reviewable_selects([select])
    assert public_selects(prepared)[0]["inference"]["review_id"] is None
    assert apply_option_review_decisions(prepared, []) == prepared


def test_public_ir_and_identity_helpers_do_not_share_mutable_state() -> None:
    ir = {
        "version": "transaction-ir/v1",
        "capture": {"capture_hash": "c", "trace_hash": "t"},
        "inputs": [{"name": "审批人"}],
        "sources": [{"url": "https://oa.example/secret"}],
        "bindings": [{"input": "审批人"}],
    }
    assert public_transaction_ir(ir) == {
        "version": "transaction-ir/v1",
        "capture": {"capture_hash": "c", "trace_hash": "t"},
        "input_count": 1,
        "source_count": 1,
        "binding_count": 1,
    }

    identity = [{"path": "applicantId", "source": "localStorage:user.id"}]
    copied = trusted_identity(identity)
    copied[0]["path"] = "changed"
    assert identity[0]["path"] == "applicantId"
