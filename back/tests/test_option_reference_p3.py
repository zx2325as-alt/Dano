from __future__ import annotations

import copy

import pytest

import dano.execution.page  # noqa: F401
import dano.orchestrator  # noqa: F401
from dano.execution.page import request_capture as rc
from dano.execution.page.option_reference_p3 import REFERENCE_VERSION, source_fingerprint
from dano.orchestrator.option_reference_p3 import (
    _decode_present_references,
    _issue_option_references,
)
from dano.orchestrator.option_reference_store_p3 import (
    MemoryOptionReferenceStore,
    OptionReferenceError,
    OptionReferenceExpired,
    OptionReferenceRecord,
    OptionReferenceScopeMismatch,
    PgOptionReferenceStore,
    set_option_reference_store,
)


def select_fixture(*, param: str = "审批人", dependency: str | None = None) -> dict:
    protocol = {}
    if dependency:
        protocol["dependencies"] = [{
            "field": dependency,
            "location": "json",
            "path": ["departmentId"],
            "required": True,
        }]
    return {
        "param": param,
        "path": "approverId" if param == "审批人" else "departmentId",
        "source_url": f"/api/options/{param}",
        "source_method": "POST",
        "source_post_data": {},
        "source_content_type": "application/json",
        "source_records_path": ["data", "rows"],
        "value_key": "id",
        "label_key": "name",
        "option_query": protocol,
    }


def api_request_fixture() -> dict:
    department = select_fixture(param="部门")
    approver = select_fixture(param="审批人", dependency="部门")
    return {
        "option_reference": {
            "version": REFERENCE_VERSION,
            "required": True,
            "legacy_raw_values": False,
        },
        "selects": [department, approver],
    }


@pytest.fixture(autouse=True)
def memory_store():
    store = MemoryOptionReferenceStore()
    set_option_reference_store(store)
    yield store
    set_option_reference_store(PgOptionReferenceStore())


@pytest.mark.asyncio
async def test_public_options_contain_only_opaque_refs(memory_store) -> None:
    select = select_fixture()
    result = await _issue_option_references(
        {
            "field": "审批人",
            "options": [{"label": "张经理", "value": 12}],
            "count": 1,
            "source_status": "ok",
            "submit_mode": "value",
        },
        tenant="tenant-a",
        skill_id="A-OA.submit_leave",
        field="审批人",
        select=select,
        context={},
    )

    assert result["submit_mode"] == "reference"
    assert result["reference_required"] is True
    token = result["options"][0]["value"]
    assert token.startswith("oref1_")
    assert "12" not in token
    assert result["options"] == [{"label": "张经理", "value": token}]

    record = await memory_store.redeem(token)
    assert record.value == 12
    assert record.tenant == "tenant-a"
    assert record.skill_id == "A-OA.submit_leave"


@pytest.mark.asyncio
async def test_cascading_refs_decode_and_bind_dependency_context(memory_store) -> None:
    api_request = api_request_fixture()
    department, approver = api_request["selects"]

    department_result = await _issue_option_references(
        {"options": [{"label": "研发部", "value": 7}], "source_status": "ok"},
        tenant="tenant-a",
        skill_id="A-OA.submit_leave",
        field="部门",
        select=department,
        context={},
    )
    department_ref = department_result["options"][0]["value"]

    decoded_context, _ = await _decode_present_references(
        api_request,
        {"部门": department_ref},
        tenant="tenant-a",
        skill_id="A-OA.submit_leave",
        require_all_dynamic_values=True,
    )
    assert decoded_context == {"部门": 7}

    approver_result = await _issue_option_references(
        {"options": [{"label": "张经理", "value": 12}], "source_status": "ok"},
        tenant="tenant-a",
        skill_id="A-OA.submit_leave",
        field="审批人",
        select=approver,
        context=decoded_context,
    )
    approver_ref = approver_result["options"][0]["value"]

    decoded, _ = await _decode_present_references(
        api_request,
        {"部门": department_ref, "审批人": approver_ref, "原因": "回家"},
        tenant="tenant-a",
        skill_id="A-OA.submit_leave",
        require_all_dynamic_values=True,
    )
    assert decoded == {"部门": 7, "审批人": 12, "原因": "回家"}

    changed_department = copy.deepcopy(decoded)
    changed_department["部门"] = 9
    with pytest.raises(OptionReferenceScopeMismatch, match="级联依赖已变化"):
        await _decode_present_references(
            api_request,
            {"部门": 9, "审批人": approver_ref},
            tenant="tenant-a",
            skill_id="A-OA.submit_leave",
            require_all_dynamic_values=False,
        )


@pytest.mark.asyncio
async def test_reference_scope_and_raw_value_fail_closed(memory_store) -> None:
    api_request = api_request_fixture()
    select = api_request["selects"][0]
    result = await _issue_option_references(
        {"options": [{"label": "研发部", "value": 7}], "source_status": "ok"},
        tenant="tenant-a",
        skill_id="A-OA.submit_leave",
        field="部门",
        select=select,
        context={},
    )
    token = result["options"][0]["value"]

    with pytest.raises(OptionReferenceScopeMismatch, match="当前租户"):
        await _decode_present_references(
            api_request,
            {"部门": token},
            tenant="tenant-b",
            skill_id="A-OA.submit_leave",
            require_all_dynamic_values=True,
        )
    with pytest.raises(OptionReferenceScopeMismatch, match="当前 Skill"):
        await _decode_present_references(
            api_request,
            {"部门": token},
            tenant="tenant-a",
            skill_id="A-OA.submit_trip",
            require_all_dynamic_values=True,
        )
    with pytest.raises(OptionReferenceError, match="必须先查询候选项"):
        await _decode_present_references(
            api_request,
            {"部门": 7},
            tenant="tenant-a",
            skill_id="A-OA.submit_leave",
            require_all_dynamic_values=True,
        )


@pytest.mark.asyncio
async def test_expired_and_unknown_references_are_rejected() -> None:
    now = [1000.0]
    store = MemoryOptionReferenceStore(clock=lambda: now[0])
    set_option_reference_store(store)
    token = await store.issue(OptionReferenceRecord(
        tenant="t",
        skill_id="s",
        field="f",
        source_fingerprint="fp",
        value=1,
        label="one",
        context_hash="",
        expires_at=1001.0,
    ))
    now[0] = 1002.0
    with pytest.raises(OptionReferenceExpired):
        await store.redeem(token)
    with pytest.raises(OptionReferenceError):
        await store.redeem("oref1_unknown_unknown_unknown")


def test_new_compiled_dynamic_select_requires_references() -> None:
    request = {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "post_data": '{"approverId":12}',
    }
    select = select_fixture()

    compiled = rc.build_api_request(
        request,
        {"approverId": "审批人"},
        selects=[select],
        typed={"审批人": 12},
    )

    assert compiled["option_reference"] == {
        "version": REFERENCE_VERSION,
        "required": True,
        "legacy_raw_values": False,
    }
    compiled_select = compiled["selects"][0]
    assert compiled_select["option_reference_required"] is True
    assert compiled_select["source_fingerprint"] == source_fingerprint(compiled_select)


def test_orchestrator_methods_are_broker_wrapped() -> None:
    from dano.orchestrator.orchestrator import Orchestrator

    assert Orchestrator.list_field_options.__name__ == "list_field_options_with_references"
    assert Orchestrator.invoke_skill.__name__ == "invoke_skill_with_references"
