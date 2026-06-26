from __future__ import annotations

import json

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page import request_capture as rc


class _Response:
    def __init__(self, data, status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        return self._data


class _Client:
    response = _Response({"rows": []})

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, *args, **kwargs):
        return type(self).response


def _select() -> dict:
    return {
        "param": "审批人",
        "source_url": "/api/users",
        "source_method": "POST",
        "source_post_data": "{}",
        "source_content_type": "application/json",
        "source_records_path": ["rows"],
        "label_key": "name",
        "value_key": "id",
    }


def _api_request() -> dict:
    return {
        "method": "POST",
        "url": "https://oa.example/api/submit",
        "body_template": {"approverId": "{{审批人}}"},
        "params": ["审批人"],
        "selects": [_select()],
    }


@pytest.mark.asyncio
async def test_same_label_with_different_values_is_rejected(monkeypatch) -> None:
    import httpx

    _Client.response = _Response({"rows": [
        {"id": 1, "name": "张经理"},
        {"id": 2, "name": "张经理"},
    ]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    result = await rc.fetch_field_options(
        _api_request(),
        "审批人",
        base_url="https://oa.example",
    )

    assert result["options"] == []
    assert result["source_status"] == "ambiguous_labels"
    assert "名称" in result["note"]


@pytest.mark.asyncio
async def test_same_label_and_value_with_different_expansion_data_is_rejected(monkeypatch) -> None:
    import httpx

    _Client.response = _Response({"rows": [
        {"id": 1, "name": "张经理", "departmentId": 10},
        {"id": 1, "name": "张经理", "departmentId": 11},
    ]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    result = await rc.fetch_field_options(
        _api_request(),
        "审批人",
        base_url="https://oa.example",
    )

    assert result["options"] == []
    assert result["source_status"] == "ambiguous_records"
    assert "附属字段" in result["note"]


@pytest.mark.asyncio
async def test_direct_skill_execution_fails_closed_on_ambiguous_label(monkeypatch) -> None:
    import httpx

    _Client.response = _Response({"rows": [
        {"id": 1, "name": "张经理"},
        {"id": 2, "name": "张经理"},
    ]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    result = await rc.execute_api_request(
        _api_request(),
        {"审批人": "张经理"},
        base_url="https://oa.example",
        send=True,
    )

    assert result["ok"] is False
    assert "相同名称" in result["detail"]


@pytest.mark.asyncio
async def test_direct_skill_execution_fails_closed_on_invalid_mapping(monkeypatch) -> None:
    import httpx

    _Client.response = _Response({"rows": [
        {"id": 1, "title": "张经理"},
    ]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    result = await rc.execute_api_request(
        _api_request(),
        {"审批人": 1},
        base_url="https://oa.example",
        send=True,
    )

    assert result["ok"] is False
    assert "显示字段或提交字段" in result["detail"]


@pytest.mark.asyncio
async def test_exact_duplicate_records_remain_safe_and_are_normalized(monkeypatch) -> None:
    import httpx

    _Client.response = _Response({"rows": [
        {"id": 1, "name": "张经理", "departmentId": 10},
        {"id": 1, "name": "张经理", "departmentId": 10},
    ]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    result = await rc.fetch_field_options(
        _api_request(),
        "审批人",
        base_url="https://oa.example",
    )

    assert result["source_status"] == "ok"
    assert result["options"] == [{"label": "张经理", "value": "1"}]
    assert result["deduplicated_count"] == 1
