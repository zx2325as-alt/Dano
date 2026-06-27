from __future__ import annotations

import json

import dano.catalog  # noqa: F401
import dano.execution.page  # noqa: F401
from dano.catalog.manifest import to_function_tool, to_manifest
from dano.execution.page.skill_interface import build_skill_interface
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel, Subsystem


def api_request_fixture() -> dict:
    return {
        "method": "POST",
        "url": "https://secret.example/api/leave/submit?token=hidden",
        "params": ["审批人", "原因"],
        "field_types": {"审批人": "enum", "原因": "string"},
        "body_template": {
            "approverId": "{{审批人}}",
            "reason": "{{原因}}",
            "internalFlowKey": "flow-secret",
        },
        "option_reference": {
            "version": "option-reference/v1",
            "required": True,
            "legacy_raw_values": False,
        },
        "selects": [{
            "param": "审批人",
            "path": "approverId",
            "tokens": ["approverId"],
            "source_url": "https://secret.example/api/users/search",
            "source_method": "POST",
            "source_post_data": {"keyword": "张", "departmentId": 7},
            "source_headers": {"X-Tenant": "secret-tenant"},
            "source_records_path": ["data", "rows"],
            "source_fingerprint": "optsrc_private_fingerprint",
            "label_key": "INTERNAL_LABEL_KEY",
            "value_key": "INTERNAL_VALUE_KEY",
            "id_path": "approverId",
            "id_tokens": ["approverId"],
            "options": [{"label": "张经理", "value": "target-id-12"}],
            "count": 1,
            "option_reference_required": True,
            "option_query": {
                "search": {"location": "json", "path": ["keyword"], "min_length": 1},
                "pagination": {"mode": "page", "location": "json", "path": ["pageNo"]},
                "dependencies": [{
                    "field": "部门",
                    "location": "json",
                    "path": ["departmentId"],
                }],
                "validation": {"location": "json", "path": ["id"]},
            },
        }],
        "identity": [{"path": "applicantId", "source": "localStorage:user.id"}],
        "derived_fields": [{"kind": "mirror", "target_path": "approverName"}],
        "success_rule": {"field": "responseCode", "ok_values": ["SECRET_OK"]},
        "fact_check": {"endpoint": "https://secret.example/api/records"},
        "transaction_ir": {
            "version": "transaction-ir/v1",
            "capture": {
                "capture_hash": "capture-public-hash",
                "trace_hash": "trace-public-hash",
                "write_event": "write:7",
            },
        },
    }


def assert_no_execution_internals(value) -> None:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    for secret in (
        "secret.example",
        "target-id-12",
        "张经理",
        "INTERNAL_LABEL_KEY",
        "INTERNAL_VALUE_KEY",
        "approverId",
        "applicantId",
        "localStorage",
        "departmentId",
        "keyword",
        "pageNo",
        "responseCode",
        "SECRET_OK",
        "flow-secret",
        "secret-tenant",
    ):
        assert secret not in rendered


def test_skill_interface_v2_exposes_capabilities_not_request_mapping() -> None:
    interface = build_skill_interface(api_request_fixture(), required_fields=["审批人", "原因"])

    assert interface["version"] == "skill-interface/v2"
    approver = interface["input_schema"]["properties"]["审批人"]
    assert approver["format"] == "option-reference"
    assert approver["x-submit-mode"] == "reference"
    assert approver["x-option-reference-required"] is True
    assert approver["x-options-search"] is True
    assert approver["x-options-pagination"] == "page"
    assert approver["x-options-depends-on"] == ["部门"]
    assert approver["x-options-validation"] is True
    assert approver["x-option-capability-id"].startswith("optcap_")

    capability = next(iter(interface["option_capabilities"].values()))
    assert capability == {
        "id": approver["x-option-capability-id"],
        "fields": ["审批人"],
        "kind": "single",
        "search": True,
        "pagination": "page",
        "depends_on": ["部门"],
        "validation": True,
        "min_query_length": 1,
        "reference_required": True,
    }
    assert interface["source_schema"] == interface["option_capabilities"]
    assert interface["bindings"] == [
        {"input": "原因", "mode": "direct"},
        {
            "input": "审批人",
            "mode": "option_reference",
            "capability_id": approver["x-option-capability-id"],
            "multiple": False,
        },
    ]
    assert interface["identity"] == {"managed_by_dano": True, "count": 1}
    assert interface["derived"] == {"managed_by_dano": True, "count": 1}
    assert interface["success"] == {"response_rule": True, "fact_check": True}
    assert interface["provenance"]["capture_hash"] == "capture-public-hash"
    assert_no_execution_internals(interface)


def test_manifest_and_function_tool_never_inline_recorded_option_ids() -> None:
    api_request = api_request_fixture()
    skill = SkillSpec(
        skill_id="A-OA.submit_leave",
        subsystem=Subsystem("A-OA"),
        action="submit_leave",
        risk_level=RiskLevel.L3,
        has_api=False,
        api_request=api_request,
        required_fields=["审批人", "原因"],
        field_types={"审批人": "enum", "原因": "string"},
        field_docs={"审批人": "审批人", "原因": "申请原因"},
    )

    manifest = to_manifest(skill)
    prop = manifest.parameters["properties"]["审批人"]
    assert prop["format"] == "option-reference"
    assert prop["x-submit-mode"] == "reference"
    assert prop["x-option-reference-required"] is True
    assert prop["x-option-reference-version"] == "option-reference/v1"
    assert "enum" not in prop
    assert "x-options" not in prop
    assert "目标系统 ID" in prop["description"]
    assert manifest.skill_interface["version"] == "skill-interface/v2"
    assert_no_execution_internals(manifest.skill_interface)

    tool = to_function_tool(manifest)
    tool_prop = tool["function"]["parameters"]["properties"]["审批人"]
    assert tool_prop["format"] == "option-reference"
    assert "enum" not in tool_prop
    assert "x-options" not in tool_prop
    assert "target-id-12" not in json.dumps(tool, ensure_ascii=False)
