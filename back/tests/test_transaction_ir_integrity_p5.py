from __future__ import annotations

import copy

import dano.execution.page  # noqa: F401
from dano.execution.page.ir_compiler import (
    compile_api_request_from_ir,
    compile_api_workflow_from_ir,
)
from dano.execution.page.ir_repair_p5 import apply_ir_fix_ops
from dano.execution.page.transaction_ir import validate_transaction_ir


def base_ir(path: str, name: str = "原因") -> dict:
    return {
        "version": "transaction-ir/v1",
        "method": "POST",
        "url": "https://oa.example/api/submit",
        "path": "/api/submit",
        "inputs": [{"name": name, "path": path, "type": "string", "sample": "回家"}],
        "bindings": [{"input": name, "target_path": path, "mode": "direct"}],
        "sources": [],
        "identity": [],
        "capture": {"capture_hash": "capture-integrity", "trace_hash": "trace-integrity"},
    }


def test_constant_partition_excludes_input_identity_and_system_time() -> None:
    request = {
        "method": "POST",
        "url": "https://oa.example/api/submit",
        "post_data": (
            '{"form":{"reason":"回家","applicantId":99,'
            '"submitTime":1760000000000,"flowKey":"leave_flow"}}'
        ),
    }
    ir = base_ir("form.reason")
    ir["identity"] = [{"path": "form.applicantId", "source": "localStorage:user.id"}]

    compiled = compile_api_request_from_ir(
        request,
        {"form.reason": "原因"},
        identity=[{
            "path": "form.applicantId",
            "tokens": ["form", "applicantId"],
            "source": "localStorage:user.id",
        }],
        typed={"原因": "回家"},
        transaction_ir=ir,
    )

    constants = compiled["transaction_ir"]["constants"]
    assert [(item["tokens"], item["value"]) for item in constants] == [
        (["form", "flowKey"], "leave_flow")
    ]
    assert compiled["system_values"] == [{
        "path": "form.submitTime",
        "tokens": ["form", "submitTime"],
        "kind": "now_ms",
    }]


def test_array_binding_owns_entire_array_and_derived_count() -> None:
    request = {
        "method": "POST",
        "url": "https://oa.example/api/meeting",
        "post_data": (
            '{"participants":[{"userId":12,"userName":"张三","participantType":2},'
            '{"userId":13,"userName":"李四","participantType":2}],'
            '"participantCount":2,"meetingKind":"weekly"}'
        ),
    }
    ir = base_ir("participants", "参会人")
    ir["inputs"][0]["type"] = "array"
    ir["bindings"][0].update({
        "mode": "expand_array",
        "source_id": "src_users",
        "target_key": "userId",
    })
    ir["sources"] = [{
        "id": "src_users",
        "kind": "http_list",
        "url": "https://oa.example/api/users",
        "method": "GET",
        "records_path": ["rows"],
        "value_key": "id",
        "label_key": "name",
    }]
    select = {
        "kind": "array",
        "path": "participants",
        "array_path": "participants",
        "array_tokens": ["participants"],
        "param": "参会人",
        "source_id": "src_users",
        "source_url": "https://oa.example/api/users",
        "source_method": "GET",
        "source_records_path": ["rows"],
        "value_key": "id",
        "label_key": "name",
        "target_key": "userId",
        "item_template": {
            "userId": {"source_key": "id"},
            "userName": {"source_key": "name"},
            "participantType": 2,
        },
        "derived_count_paths": [{
            "path": "participantCount",
            "tokens": ["participantCount"],
        }],
        "sample_values": [12, 13],
    }

    compiled = compile_api_request_from_ir(
        request,
        {"participants": "参会人"},
        selects=[select],
        typed={"参会人": [12, 13]},
        transaction_ir=ir,
    )

    constants = compiled["transaction_ir"]["constants"]
    assert [(item["tokens"], item["value"]) for item in constants] == [
        (["meetingKind"], "weekly")
    ]
    array_binding = compiled["transaction_ir"]["bindings"][0]
    assert array_binding["target_tokens"] == ["participants"]
    assert array_binding["derived_count_paths"][0]["tokens"] == ["participantCount"]


def test_validator_rejects_two_owners_for_same_location() -> None:
    request = {
        "method": "POST",
        "url": "https://oa.example/api/submit",
        "post_data": '{"form":{"reason":"回家","flowKey":"leave_flow"}}',
    }
    compiled = compile_api_request_from_ir(
        request,
        {"form.reason": "原因"},
        typed={"原因": "回家"},
        transaction_ir=base_ir("form.reason"),
    )
    ir = copy.deepcopy(compiled["transaction_ir"])
    ir["identity"] = [{
        "path": "form.reason",
        "tokens": ["form", "reason"],
        "step": 0,
        "source": "localStorage:user.id",
    }]

    issues = validate_transaction_ir(ir)

    assert any("conflicts with bindings[0]" in issue for issue in issues)


def workflow_fixture(*, automatic_link: bool) -> dict:
    first = {
        "method": "POST",
        "url": "https://oa.example/api/draft",
        "post_data": '{"draft":{"reason":"回家"}}',
        "response_json": {"code": 0, "data": {"taskId": "TASK-0001"}},
    }
    task_id = "TASK-0001" if automatic_link else "UNRELATED"
    second = {
        "method": "POST",
        "url": "https://oa.example/api/submit",
        "post_data": '{"form":{"reason":"回家","taskId":"' + task_id + '"}}',
        "response_json": {"code": 0},
    }
    ir = base_ir("form.reason")
    return compile_api_workflow_from_ir(
        [first, second],
        param_map={"form.reason": "原因"},
        typed={"原因": "回家"},
        transaction_ir=ir,
    )


def test_failed_reorder_rolls_back_every_ir_mutation() -> None:
    compiled = workflow_fixture(automatic_link=True)
    assert compiled["transaction_ir"]["execution"]["links"]

    repaired, applied, rejected = apply_ir_fix_ops(compiled, [
        {"op": "reorder_steps", "order": [1, 0]},
    ])

    assert applied == []
    assert rejected and "依赖倒置" in rejected[0]["detail"]
    assert repaired == compiled


def test_link_patch_requires_captured_source_response_path() -> None:
    compiled = workflow_fixture(automatic_link=False)

    repaired, applied, rejected = apply_ir_fix_ops(compiled, [{
        "op": "link_step",
        "source_step": 0,
        "source_path": "data.missing",
        "target_step": 1,
        "target_path": "form.taskId",
    }])

    assert applied == []
    assert rejected and "来源步响应里不存在" in rejected[0]["detail"]
    assert repaired == compiled
