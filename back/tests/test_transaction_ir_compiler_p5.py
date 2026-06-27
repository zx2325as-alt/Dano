from __future__ import annotations

import copy

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page.ir_compiler import (
    canonicalize_api_request,
    compile_api_request_from_ir,
    compile_api_workflow_from_ir,
    compile_transaction_ir,
    is_ir_authoritative,
)
from dano.execution.page.ir_repair_p5 import apply_ir_fix_ops
from dano.execution.page.transaction_authority_p4 import seal_api_request


def draft_ir() -> dict:
    return {
        "version": "transaction-ir/v1",
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "path": "/api/leave/submit",
        "inputs": [
            {
                "name": "原因",
                "path": "form.reason",
                "type": "string",
                "required": True,
                "sample": "回家",
            },
            {
                "name": "审批人",
                "path": "form.approverId",
                "type": "select",
                "required": True,
                "sample": "12",
                "source_id": "src_users",
            },
        ],
        "sources": [
            {
                "id": "src_users",
                "kind": "http_list",
                "url": "https://oa.example/api/users/search",
                "method": "POST",
                "records_path": ["data", "rows"],
                "value_key": "id",
                "label_key": "name",
            }
        ],
        "bindings": [
            {"input": "原因", "target_path": "form.reason", "mode": "direct"},
            {
                "input": "审批人",
                "target_path": "form.approverId",
                "mode": "select_value",
                "source_id": "src_users",
                "target_key": "id",
            },
        ],
        "identity": [
            {"path": "form.applicantId", "source": "localStorage:user.id"}
        ],
        "capture": {"capture_hash": "capture-p5", "trace_hash": "trace-p5"},
    }


def request_fixture() -> dict:
    return {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "content_type": "application/json",
        "headers": {"Authorization": "Bearer secret", "User-Agent": "ignored"},
        "post_data": (
            '{"form":{"reason":"回家","approverId":12,'
            '"applicantId":99,"flowKey":"leave_flow"}}'
        ),
        "response_json": {"code": 0, "message": "ok"},
    }


def select_fixture() -> dict:
    return {
        "path": "form.approverId",
        "tokens": ["form", "approverId"],
        "label": "张经理",
        "source_id": "src_users",
        "source_url": "https://oa.example/api/users/search",
        "source_method": "POST",
        "source_post_data": {"keyword": "张", "pageNo": 1},
        "source_content_type": "application/json",
        "source_headers": {"X-Tenant": "tenant-a"},
        "source_records_path": ["data", "rows"],
        "value_key": "id",
        "label_key": "name",
        "options": [{"label": "张经理", "value": "12"}],
        "count": 1,
        "option_query": {
            "search": {"location": "json", "path": ["keyword"]},
            "pagination": {"mode": "page", "location": "json", "path": ["pageNo"]},
        },
    }


def compile_single() -> dict:
    return compile_api_request_from_ir(
        request_fixture(),
        {"form.reason": "原因", "form.approverId": "审批人"},
        selects=[select_fixture()],
        identity=[{"path": "form.applicantId", "tokens": ["form", "applicantId"],
                   "source": "localStorage:user.id"}],
        typed={"原因": "回家", "审批人": "张经理"},
        transaction_ir=draft_ir(),
    )


def test_direct_compiler_does_not_call_legacy_builders(monkeypatch) -> None:
    from dano.execution.page import request_capture

    monkeypatch.setattr(request_capture, "build_api_request", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("legacy builder must not be called")
    ))
    monkeypatch.setattr(request_capture, "build_api_workflow", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("legacy workflow builder must not be called")
    ))

    compiled = compile_single()

    assert is_ir_authoritative(compiled) is True
    assert compiled["transaction_ir"]["compile"] == {
        "compiler": "transaction-ir/p5",
        "source_of_truth": "transaction_ir",
        "param_paths": ["form.approverId", "form.reason"],
        "query_source_count": 1,
    }
    assert compiled["body_template"]["form"]["reason"] == "{{原因}}"
    assert compiled["body_template"]["form"]["approverId"] == "{{审批人}}"
    assert compiled["identity"][0]["tokens"] == ["form", "applicantId"]
    assert compiled["auth_headers"] == {"Authorization": "Bearer secret"}
    assert compiled["success_rule"] == {"field": "code", "ok_values": ["0"]}
    assert compiled["option_reference"]["required"] is True
    assert compiled["selects"][0]["source_method"] == "POST"
    assert compiled["selects"][0]["source_post_data"] == {"keyword": "张", "pageNo": 1}


def test_same_ir_recompiles_to_same_artifact() -> None:
    compiled = compile_single()
    transaction_ir = copy.deepcopy(compiled["transaction_ir"])

    again = compile_transaction_ir(transaction_ir)

    assert again == compiled


def test_canonicalization_discards_direct_executable_mutation_and_absorbs_assertions() -> None:
    compiled = compile_single()
    mutated = copy.deepcopy(compiled)
    mutated["body_template"]["form"]["reason"] = "ATTACKER-CONTROLLED"
    mutated["fact_check"] = {
        "endpoint": "/api/leave/mine",
        "match_field": "reason",
        "param": "原因",
    }
    mutated["goal"] = {"intent": "提交请假"}

    canonical = canonicalize_api_request(mutated)

    assert canonical["body_template"]["form"]["reason"] == "{{原因}}"
    assert canonical["transaction_ir"]["fact_check"] == mutated["fact_check"]
    assert canonical["transaction_ir"]["goal"] == mutated["goal"]
    assert canonical["fact_check"] == mutated["fact_check"]


def test_publish_seal_uses_p5_compiler_and_canonical_projection() -> None:
    compiled = compile_single()
    compiled["body_template"]["form"]["reason"] = "direct-mutation"

    sealed = seal_api_request(compiled)

    assert sealed["body_template"]["form"]["reason"] == "{{原因}}"
    assert sealed["transaction_ir"]["authority"]["compiler_version"] == "transaction-ir/p5"


def test_ir_only_repair_renames_and_remaps_then_recompiles() -> None:
    compiled = compile_single()

    repaired, applied, rejected = apply_ir_fix_ops(compiled, [
        {"op": "rename_param", "old": "原因", "new": "请假原因"},
        {"op": "remap_field", "param": "请假原因", "target_path": "form.flowKey", "step": 0},
    ])

    assert rejected == []
    assert len(applied) == 2
    assert repaired["params"] == ["请假原因", "审批人"]
    assert repaired["body_template"]["form"]["reason"] == "回家"
    assert repaired["body_template"]["form"]["flowKey"] == "{{请假原因}}"
    binding = next(item for item in repaired["transaction_ir"]["bindings"]
                   if item["input"] == "请假原因")
    assert binding["target_tokens"] == ["form", "flowKey"]


def test_invalid_ir_patch_rolls_back_without_changing_artifact() -> None:
    compiled = compile_single()

    repaired, applied, rejected = apply_ir_fix_ops(compiled, [
        {"op": "remap_field", "param": "原因", "target_path": "missing.path", "step": 0},
    ])

    assert applied == []
    assert len(rejected) == 1
    assert repaired == compiled


def _compiled_workflow() -> dict:
    first = {
        "method": "POST",
        "url": "https://oa.example/api/leave/draft",
        "post_data": '{"draft":{"reason":"回家"}}',
        "response_json": {"code": 0, "data": {"taskId": "T-1"}},
    }
    second = {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "post_data": '{"form":{"reason":"回家","taskId":"T-1"}}',
        "response_json": {"code": 0},
    }
    ir = draft_ir()
    ir["inputs"] = [{"name": "原因", "path": "form.reason", "type": "string", "sample": "回家"}]
    ir["bindings"] = [{"input": "原因", "target_path": "form.reason", "mode": "direct"}]
    ir["sources"] = []
    ir["identity"] = []
    return compile_api_workflow_from_ir(
        [first, second],
        param_map={"form.reason": "原因"},
        typed={"原因": "回家"},
        transaction_ir=ir,
    )


def test_workflow_is_materialized_and_compiled_from_ir() -> None:
    compiled = _compiled_workflow()

    assert is_ir_authoritative(compiled)
    assert compiled["transaction_ir"]["execution"]["kind"] == "workflow"
    assert len(compiled["steps"]) == 2
    assert compiled["steps"][-1]["body_template"]["form"]["reason"] == "{{原因}}"
    assert compiled["params"] == ["原因"]


def test_constants_are_a_canonical_partition_not_a_second_owner() -> None:
    compiled = compile_single()

    constants = compiled["transaction_ir"]["constants"]

    assert [(item["tokens"], item["value"]) for item in constants] == [
        (["form", "flowKey"], "leave_flow")
    ]
    assert all(item["tokens"] not in (
        ["form", "reason"], ["form", "approverId"], ["form", "applicantId"]
    ) for item in constants)


def test_failed_reorder_restores_the_ir_and_artifact() -> None:
    compiled = _compiled_workflow()
    assert compiled["transaction_ir"]["execution"]["links"]

    repaired, applied, rejected = apply_ir_fix_ops(compiled, [
        {"op": "reorder_steps", "order": [1, 0]},
    ])

    assert applied == []
    assert rejected and "依赖倒置" in rejected[0]["detail"]
    assert repaired == compiled


@pytest.mark.asyncio
async def test_repair_loop_routes_p5_assets_to_ir_executor() -> None:
    from dano.onboarding.repair import run_repair_loop

    compiled = compile_single()
    compiled["params"][0] = "directly-corrupted-name"

    async def proposer(_artifact, _findings, _goal):
        return [{"op": "rename_param", "old": "原因", "new": "请假原因"}]

    repaired, rounds, history, remaining = await run_repair_loop(
        compiled,
        proposer,
        seed_findings=[{"kind": "review_acceptance", "detail": "参数名应更明确"}],
    )

    assert rounds >= 1
    assert history[0]["source"] == "transaction_ir"
    assert repaired["params"][0] == "请假原因"
    assert remaining == []
