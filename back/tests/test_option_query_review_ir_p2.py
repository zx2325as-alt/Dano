from dano.execution.page.option_query_review_p2 import (
    apply_option_review_decisions,
    prepare_reviewable_selects,
    public_selects,
    synchronize_transaction_ir,
)
from dano.execution.page.transaction_ir import stable_source_id


def select_fixture() -> dict:
    return {
        "path": "approverId",
        "source_url": "https://oa.example/api/users/search",
        "value_key": "id",
        "label_key": "name",
        "option_query": {"search": {"location": "json", "path": ["keyword"]}},
        "option_query_inference": {
            "status": "inferred",
            "confidence": 0.94,
            "confirmed_by_user": False,
            "source_fingerprint": "fp",
            "evidence": [{"kind": "search", "evidence_refs": ["ui:1", "read:2"]}],
        },
    }


def ir_fixture() -> dict:
    return {
        "version": "transaction-ir/v1",
        "sources": [{
            "id": stable_source_id(
                "https://oa.example/api/users/search",
                "id",
                "name",
            ),
            "kind": "http_list",
            "url": "https://oa.example/api/users/search",
            "query_protocol": {"search": {"location": "json", "path": ["keyword"]}},
            "inference": {"status": "inferred", "confidence": 0.94},
        }],
    }


def test_accept_updates_ir_inference_status() -> None:
    server = prepare_reviewable_selects([select_fixture()])
    review_id = public_selects(server)[0]["inference"]["review_id"]
    accepted = apply_option_review_decisions(server, [{"review_id": review_id, "decision": "accept"}])

    synchronized = synchronize_transaction_ir(ir_fixture(), accepted)

    source = synchronized["sources"][0]
    assert source["query_protocol"] == accepted[0]["option_query"]
    assert source["inference"]["status"] == "confirmed"
    assert source["inference"]["confirmed_by_user"] is True


def test_reject_removes_protocol_and_inference_from_ir() -> None:
    server = prepare_reviewable_selects([select_fixture()])
    review_id = public_selects(server)[0]["inference"]["review_id"]
    rejected = apply_option_review_decisions(server, [{"review_id": review_id, "decision": "reject"}])

    synchronized = synchronize_transaction_ir(ir_fixture(), rejected)

    source = synchronized["sources"][0]
    assert "query_protocol" not in source
    assert "inference" not in source


def test_sync_does_not_mutate_original_ir() -> None:
    original = ir_fixture()
    reviewed = [select_fixture()]
    reviewed[0]["option_query_inference"]["status"] = "confirmed"

    synchronized = synchronize_transaction_ir(original, reviewed)

    assert synchronized is not original
    assert original["sources"][0]["inference"]["status"] == "inferred"
