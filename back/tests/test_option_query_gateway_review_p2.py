from __future__ import annotations

import copy

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page.transaction_ir import stable_source_id
from dano.gateway.app import _request_fields_msg, _trusted_transaction_ir


_SELECT = {
    "path": "approverId",
    "label": "张经理",
    "count": 20,
    "source_url": "https://oa.example/api/users/search",
    "source_method": "POST",
    "source_post_data": {"keyword": "张", "pageNo": 1},
    "source_headers": {"X-Tenant": "T1"},
    "source_records_path": ["data", "rows"],
    "value_key": "id",
    "label_key": "name",
    "option_query": {
        "search": {"location": "json", "path": ["keyword"]},
        "pagination": {"mode": "page", "location": "json", "path": ["pageNo"]},
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


def _server_ir(select: dict) -> dict:
    return {
        "version": "transaction-ir/v1",
        "inputs": [{"name": "审批人", "path": "approverId"}],
        "sources": [{
            "id": stable_source_id(select["source_url"], select["value_key"], select["label_key"]),
            "kind": "http_list",
            "url": select["source_url"],
            "method": select["source_method"],
            "records_path": select["source_records_path"],
            "value_key": select["value_key"],
            "label_key": select["label_key"],
            "query_protocol": copy.deepcopy(select["option_query"]),
            "inference": copy.deepcopy(select["option_query_inference"]),
        }],
        "bindings": [{
            "input": "审批人",
            "target_path": "approverId",
            "mode": "select_value",
            "source_id": stable_source_id(select["source_url"], select["value_key"], select["label_key"]),
        }],
        "capture": {"capture_hash": "capture-a", "trace_hash": "trace-a"},
    }


@pytest.mark.asyncio
async def test_request_fields_public_projection_hides_source_and_identity_details(monkeypatch) -> None:
    from dano.agent_tools import tools as agent_tools
    from dano.execution.page import dataflow

    select = copy.deepcopy(_SELECT)
    server_ir = _server_ir(select)

    def fake_infer(*args, **kwargs):
        return {
            "fields": [{
                "path": "approverId",
                "key": "approverId",
                "value": "12",
                "suggest_param": True,
                "suggest_name": "审批人",
                "type": "number",
                "required": True,
            }],
            "selects": [copy.deepcopy(select)],
            "identity": [{"path": "applicantId", "source": "localStorage:user.id"}],
            "suggested_steps": [0],
            "derived_mirrors": [],
            "transaction_ir": copy.deepcopy(server_ir),
        }

    def fake_build(*args, **kwargs):
        return copy.deepcopy(server_ir)

    monkeypatch.setattr(agent_tools, "_review_board", None)
    monkeypatch.setattr(dataflow, "infer_request_transaction", fake_infer)
    monkeypatch.setattr(dataflow, "build_transaction_ir", fake_build)

    chosen = {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "post_data": '{"approverId":12}',
    }
    message = await _request_fields_msg(
        chosen,
        [chosen],
        {"审批人": "张经理"},
        trace_ir={"version": "trace-ir/v1", "capture_hash": "capture-a", "trace_hash": "trace-a"},
    )

    assert message["url"] == "/api/leave/submit"
    assert message["identity"] == [{"path": "applicantId"}]
    assert message["transaction_ir"] == {
        "version": "transaction-ir/v1",
        "capture": {"capture_hash": "capture-a", "trace_hash": "trace-a"},
        "input_count": 1,
        "source_count": 1,
        "binding_count": 1,
    }
    public_select = message["selects"][0]
    assert public_select["capabilities"] == {
        "search": True,
        "pagination": "page",
        "validation": False,
        "dependencies": [],
    }
    assert public_select["inference"]["review_id"].startswith("oqr_")

    # The websocket handler removes these private keys before send_json. Their contents
    # remain authoritative server state and are never accepted back from the browser.
    server_selects = message.pop("_server_selects")
    server_identity = message.pop("_server_identity")
    private_ir = message.pop("_server_transaction_ir")
    assert server_selects[0]["source_url"].startswith("https://oa.example")
    assert server_identity[0]["source"] == "localStorage:user.id"
    assert private_ir["sources"][0]["query_protocol"]["search"]["path"] == ["keyword"]

    public_dump = repr(message)
    for secret in (
        "oa.example", "source_url", "source_post_data", "source_headers",
        "value_key", "label_key", "localStorage", "keyword", "pageNo",
    ):
        assert secret not in public_dump


def test_client_only_transaction_ir_is_never_preferred_over_server_ir() -> None:
    server = _server_ir(_SELECT)
    client = copy.deepcopy(server)
    client["sources"][0]["url"] = "https://attacker.example/options"

    assert _trusted_transaction_ir(server, client, {"trace_hash": "trace-a"}) == server
    assert _trusted_transaction_ir(None, client, {"trace_hash": "trace-a"}) is None
