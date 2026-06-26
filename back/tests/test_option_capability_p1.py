from __future__ import annotations

from types import SimpleNamespace

import pytest

import dano.execution.page  # noqa: F401
from dano.catalog.manifest import _public_skill_interface, _schema_prop
from dano.execution.page import option_p0
from dano.execution.page.option_query import query_field_options


@pytest.mark.asyncio
async def test_static_options_support_search_and_cursor_pagination() -> None:
    api_request = {
        "selects": [{
            "param": "城市",
            "options": [
                {"label": "上海", "value": "sh"},
                {"label": "上饶", "value": "sr"},
                {"label": "北京", "value": "bj"},
            ],
            "submit_mode": "value",
        }],
    }

    first = await query_field_options(api_request, "城市", query="上", limit=1)
    assert first["protocol_version"] == "option-query/v1"
    assert first["options"] == [{"label": "上海", "value": "sh"}]
    assert first["count"] == 2
    assert first["has_more"] is True
    assert first["next_cursor"]

    second = await query_field_options(
        api_request, "城市", query="上", limit=1, cursor=first["next_cursor"])
    assert second["options"] == [{"label": "上饶", "value": "sr"}]
    assert second["has_more"] is False


@pytest.mark.asyncio
async def test_cursor_is_bound_to_query_and_context() -> None:
    api_request = {
        "selects": [{
            "param": "城市",
            "options": [{"label": "上海", "value": "sh"}, {"label": "北京", "value": "bj"}],
        }],
    }
    first = await query_field_options(api_request, "城市", limit=1)
    changed = await query_field_options(
        api_request, "城市", query="北", limit=1, cursor=first["next_cursor"])
    assert changed["source_status"] == "invalid_cursor"
    assert changed["options"] == []


@pytest.mark.asyncio
async def test_missing_cascade_context_stops_before_source_call(monkeypatch) -> None:
    called = False

    async def fake_fetch(*args, **kwargs):
        nonlocal called
        called = True
        return [], {"ok": True, "status": 200}

    monkeypatch.setattr(option_p0, "_fetch_options", fake_fetch)
    api_request = {
        "selects": [{
            "param": "审批人",
            "source_url": "/api/users",
            "value_key": "id",
            "label_key": "name",
            "source_input_bindings": [{
                "from": "context.部门",
                "target": "body",
                "tokens": ["deptId"],
                "required": True,
            }],
        }],
    }

    result = await query_field_options(api_request, "审批人", context={})
    assert called is False
    assert result["source_status"] == "needs_context"
    assert result["dependencies"] == ["部门"]


@pytest.mark.asyncio
async def test_query_and_context_are_bound_inside_backend(monkeypatch) -> None:
    captured = {}

    async def fake_fetch(select, **kwargs):
        captured.update(select)
        return [
            {"id": 1, "name": "张经理"},
            {"id": 2, "name": "李经理"},
        ], {"ok": True, "status": 200, "source_status": "ok", "message": ""}

    monkeypatch.setattr(option_p0, "_fetch_options", fake_fetch)
    api_request = {
        "auth_headers": {"Authorization": "Bearer runtime"},
        "selects": [{
            "param": "审批人",
            "source_url": "/api/users/search",
            "source_method": "POST",
            "source_post_data": {"keyword": "", "deptId": None},
            "source_content_type": "application/json",
            "source_records_path": ["rows"],
            "value_key": "id",
            "label_key": "name",
            "source_input_bindings": [
                {"from": "query", "target": "body", "tokens": ["keyword"]},
                {"from": "context.部门", "target": "body", "tokens": ["deptId"]},
            ],
        }],
    }

    result = await query_field_options(
        api_request, "审批人", query="张", context={"部门": 7}, limit=20)

    assert captured["source_post_data"] == {"keyword": "张", "deptId": 7}
    assert result["options"] == [{"label": "张经理", "value": "1"}]
    assert result["source_status"] == "ok"


def test_public_interface_strips_target_system_details() -> None:
    public = _public_skill_interface({
        "version": "skill-interface/v1",
        "input_schema": {"type": "object"},
        "source_schema": {
            "src_users": {
                "id": "src_users",
                "url": "https://oa.internal/api/users",
                "method": "POST",
                "value_key": "userId",
                "label_key": "name",
                "option_filter": {"status": 1},
                "fields": ["审批人"],
                "submit_modes": ["value"],
                "count": 100,
            },
        },
        "bindings": [{
            "input": "审批人",
            "mode": "select_value",
            "source_id": "src_users",
            "target_path": "form.approverId",
            "target_tokens": ["form", "approverId"],
        }],
        "identity": [{"path": "creatorId", "source": "localStorage:user.id"}],
        "success": {"path": "code", "equals": 200},
    })

    source = public["source_schema"]["src_users"]
    assert source["protocol"] == "option-query/v1"
    assert "url" not in source
    assert "method" not in source
    assert "value_key" not in source
    assert "label_key" not in source
    assert "option_filter" not in source
    assert "target_path" not in public["bindings"][0]
    assert "identity" not in public
    assert public["success"] == {"configured": True}


def test_dynamic_schema_does_not_publish_recorded_snapshot() -> None:
    skill = SimpleNamespace(field_types={"审批人": "enum"})
    prop = _schema_prop(skill, "审批人", "审批人", {
        "source_url": "/api/users",
        "options": [{"label": "旧用户", "value": "12"}],
        "source_input_bindings": [{"from": "context.部门", "target": "body", "tokens": ["deptId"]}],
    })

    assert prop["x-options-source"] is True
    assert prop["x-options-protocol"] == "option-query/v1"
    assert prop["x-option-depends-on"] == ["部门"]
    assert "x-options" not in prop
    assert "enum" not in prop
