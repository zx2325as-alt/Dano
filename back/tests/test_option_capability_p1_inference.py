from __future__ import annotations

import json

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page import option_p0
from dano.execution.page import request_capture as rc
from dano.execution.page.option_query import query_field_options


def test_recording_infers_search_pagination_and_cascade_bindings() -> None:
    submit = '{"approverId":12}'
    reads = [{
        "method": "POST",
        "url": "https://oa.example/api/users?keyword=&pageNum=1&pageSize=20",
        "post_data": '{"deptId":7}',
        "content_type": "application/json",
        "json": {"rows": [{"userId": 12, "nickName": "张经理"}]},
    }]

    selects = rc.suggest_selects(submit, reads, {"部门": "7"})

    assert len(selects) == 1
    select = selects[0]
    assert select["source_url"] == "https://oa.example/api/users"
    assert select["source_query"] == {"keyword": "", "pageNum": "1", "pageSize": "20"}
    assert select["source_input_bindings"] == [
        {"from": "query", "target": "query", "tokens": ["keyword"], "value_type": "string"},
        {"from": "page", "target": "query", "tokens": ["pageNum"],
         "value_type": "integer", "page_base": 1},
        {"from": "limit", "target": "query", "tokens": ["pageSize"], "value_type": "integer"},
        {"from": "context.部门", "target": "body", "tokens": ["deptId"],
         "value_type": "integer", "required": True},
    ]

    compiled = rc.build_api_request(
        {"method": "POST", "url": "/submit", "post_data": submit},
        {"approverId": "审批人"},
        selects=selects,
    )
    assert compiled is not None
    assert compiled["selects"][0]["source_input_bindings"] == select["source_input_bindings"]


def test_ambiguous_sample_value_is_not_inferred_as_context() -> None:
    submit = '{"approverId":12}'
    reads = [{
        "method": "POST",
        "url": "/api/users",
        "post_data": '{"deptId":7}',
        "content_type": "application/json",
        "json": {"rows": [{"userId": 12, "nickName": "张经理"}]},
    }]

    select = rc.suggest_selects(submit, reads, {"部门": "7", "公司": "7"})[0]
    assert "source_input_bindings" not in select


@pytest.mark.asyncio
async def test_invalid_binding_stops_before_target_request(monkeypatch) -> None:
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
            "source_input_bindings": [{"from": "query", "target": "body", "tokens": []}],
        }],
    }

    result = await query_field_options(api_request, "审批人", query="张")
    assert called is False
    assert result["source_status"] == "invalid_binding"
    assert "目标路径" in result["note"]


@pytest.mark.asyncio
async def test_oversized_context_is_rejected() -> None:
    api_request = {"selects": [{"param": "审批人", "options": ["张经理"]}]}
    context = {f"字段{i}": i for i in range(65)}

    result = await query_field_options(api_request, "审批人", context=context)
    assert result["source_status"] == "invalid_context"
    assert result["options"] == []


@pytest.mark.asyncio
async def test_partial_filtered_upstream_page_does_not_drop_next_candidate(monkeypatch) -> None:
    calls: list[int] = []

    async def fake_fetch(select, **kwargs):
        offset = int(select["source_query"]["offset"])
        calls.append(offset)
        pages = {
            0: [{"id": 0, "name": "李一"}, {"id": 1, "name": "张一"}],
            2: [{"id": 2, "name": "张二"}, {"id": 3, "name": "王一"}],
        }
        rows = pages.get(offset, [])
        return rows, {"ok": True, "status": 200, "source_status": "ok",
                      "message": "", "raw_count": len(rows)}

    monkeypatch.setattr(option_p0, "_fetch_options", fake_fetch)
    api_request = {
        "selects": [{
            "param": "审批人",
            "source_url": "/api/users",
            "source_query": {"offset": 0, "limit": 0},
            "value_key": "id",
            "label_key": "name",
            "source_input_bindings": [
                {"from": "offset", "target": "query", "tokens": ["offset"], "value_type": "integer"},
                {"from": "limit", "target": "query", "tokens": ["limit"], "value_type": "integer"},
            ],
        }],
    }

    first = await query_field_options(api_request, "审批人", query="张", limit=2)
    assert first["options"] == [{"label": "张一", "value": "1"}]
    assert first["has_more"] is True

    second = await query_field_options(
        api_request, "审批人", query="张", limit=2, cursor=first["next_cursor"])
    assert calls == [0, 2]
    assert second["options"] == [{"label": "张二", "value": "2"}]


@pytest.mark.asyncio
async def test_page_number_binding_uses_recorded_base(monkeypatch) -> None:
    captured: list[dict] = []

    async def fake_fetch(select, **kwargs):
        captured.append(json.loads(json.dumps(select)))
        page = int(select["source_query"]["pageNum"])
        rows = [{"id": page, "name": f"第{page}页"}]
        return rows, {"ok": True, "status": 200, "source_status": "ok",
                      "message": "", "raw_count": 1}

    monkeypatch.setattr(option_p0, "_fetch_options", fake_fetch)
    api_request = {
        "selects": [{
            "param": "页面",
            "source_url": "/api/pages",
            "source_query": {"pageNum": 1, "pageSize": 1},
            "value_key": "id",
            "label_key": "name",
            "source_input_bindings": [
                {"from": "page", "target": "query", "tokens": ["pageNum"],
                 "value_type": "integer", "page_base": 1},
                {"from": "limit", "target": "query", "tokens": ["pageSize"],
                 "value_type": "integer"},
            ],
        }],
    }

    first = await query_field_options(api_request, "页面", limit=1)
    second = await query_field_options(
        api_request, "页面", limit=1, cursor=first["next_cursor"])

    assert captured[0]["source_query"]["pageNum"] == 1
    assert captured[1]["source_query"]["pageNum"] == 2
    assert second["options"] == [{"label": "第2页", "value": "2"}]
