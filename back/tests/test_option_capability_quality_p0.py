from __future__ import annotations

import json

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page import option_p0_quality
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
    called = 0
    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, **kwargs):
        type(self).called += 1
        type(self).calls.append({"method": method, "url": url, **kwargs})
        return type(self).response


def _api_request(label_key: str = "name", value_key: str = "id") -> dict:
    return {
        "selects": [{
            "param": "审批人",
            "source_url": "/api/users",
            "source_method": "POST",
            "source_post_data": "{}",
            "source_content_type": "application/json",
            "source_records_path": ["rows"],
            "label_key": label_key,
            "value_key": value_key,
        }]
    }


def test_recorded_sensitive_post_body_is_redacted_in_select_metadata() -> None:
    submit = '{"approverId":12}'
    reads = [{
        "method": "POST",
        "url": "https://oa.example/api/users",
        "post_data": '{"departmentId":7,"accessToken":"secret-value"}',
        "content_type": "application/json",
        "records_path": ["rows"],
        "json": {"rows": [{"id": 12, "name": "张经理"}]},
    }]

    selects = rc.suggest_selects(submit, reads)

    assert len(selects) == 1
    select = selects[0]
    assert "secret-value" not in select["source_post_data"]
    assert "__DANO_REDACTED__" in select["source_post_data"]
    assert select["source_body_redacted"] is True
    assert select["source_sensitive_body_keys"] == ["accessToken"]


def test_compiler_binds_option_source_to_target_origin() -> None:
    request = {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "post_data": '{"approverId":12}',
        "content_type": "application/json",
        "headers": {},
    }
    select = {
        "path": "approverId",
        "tokens": ["approverId"],
        "source_url": "/api/users",
        "source_method": "POST",
        "source_post_data": "{}",
        "source_content_type": "application/json",
        "source_records_path": ["rows"],
        "label_key": "name",
        "value_key": "id",
    }

    compiled = rc.build_api_request(request, {"approverId": "审批人"}, selects=[select])

    assert compiled is not None
    assert compiled["selects"][0]["source_target_origin"] == "https://oa.example"


@pytest.mark.asyncio
async def test_sensitive_source_body_is_blocked_before_network(monkeypatch) -> None:
    import httpx

    _Client.called = 0
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    request = _api_request()
    request["selects"][0]["source_post_data"] = '{"accessToken":"secret-value"}'

    result = await rc.fetch_field_options(
        request,
        "审批人",
        base_url="https://oa.example",
    )

    assert _Client.called == 0
    assert result["source_status"] == "sensitive_request"
    assert "凭证" in result["note"]


@pytest.mark.asyncio
async def test_target_request_origin_resolves_relative_source_without_explicit_base(monkeypatch) -> None:
    import httpx

    _Client.called = 0
    _Client.calls = []
    _Client.response = _Response({"rows": [{"id": 1, "name": "张经理"}]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    request = _api_request()
    request["url"] = "https://oa.example/api/leave/submit"

    result = await rc.fetch_field_options(request, "审批人")

    assert result["source_status"] == "ok"
    assert _Client.calls[0]["url"] == "https://oa.example/api/users"


@pytest.mark.asyncio
async def test_target_request_origin_blocks_absolute_cross_origin_source(monkeypatch) -> None:
    import httpx

    _Client.called = 0
    _Client.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    request = _api_request()
    request["url"] = "https://oa.example/api/leave/submit"
    request["selects"][0]["source_url"] = "https://evil.example/api/users"

    result = await rc.fetch_field_options(request, "审批人")

    assert _Client.called == 0
    assert result["source_status"] == "cross_origin_blocked"


@pytest.mark.asyncio
async def test_nonempty_response_with_wrong_mapping_is_not_reported_as_empty(monkeypatch) -> None:
    import httpx

    _Client.response = _Response({"rows": [{"id": 1, "title": "张经理"}]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    result = await rc.fetch_field_options(
        _api_request(label_key="name"),
        "审批人",
        base_url="https://oa.example",
    )

    assert result["options"] == []
    assert result["source_status"] == "invalid_mapping"
    assert "显示字段" in result["note"]


@pytest.mark.asyncio
async def test_exact_duplicate_options_are_deduplicated(monkeypatch) -> None:
    import httpx

    _Client.response = _Response({"rows": [
        {"id": 1, "name": "张经理"},
        {"id": 1, "name": "张经理"},
        {"id": 2, "name": "李经理"},
    ]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    result = await rc.fetch_field_options(
        _api_request(),
        "审批人",
        base_url="https://oa.example",
    )

    assert result["source_status"] == "ok"
    assert result["options"] == [
        {"label": "张经理", "value": "1"},
        {"label": "李经理", "value": "2"},
    ]
    assert result["deduplicated_count"] == 1


@pytest.mark.asyncio
async def test_same_value_with_different_labels_is_rejected(monkeypatch) -> None:
    import httpx

    _Client.response = _Response({"rows": [
        {"id": 1, "name": "张经理"},
        {"id": 1, "name": "同名旧记录"},
    ]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    result = await rc.fetch_field_options(
        _api_request(),
        "审批人",
        base_url="https://oa.example",
    )

    assert result["options"] == []
    assert result["source_status"] == "ambiguous_values"
    assert "多个名称" in result["note"]


@pytest.mark.asyncio
async def test_oversized_option_source_is_rejected(monkeypatch) -> None:
    import httpx

    _Client.response = _Response({"rows": [
        {"id": 1, "name": "A"},
        {"id": 2, "name": "B"},
        {"id": 3, "name": "C"},
    ]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(option_p0_quality, "_MAX_SOURCE_ITEMS", 2)

    result = await rc.fetch_field_options(
        _api_request(),
        "审批人",
        base_url="https://oa.example",
    )

    assert result["options"] == []
    assert result["source_status"] == "too_many_options"
    assert "超过安全上限" in result["note"]


@pytest.mark.asyncio
async def test_oversized_json_payload_is_rejected(monkeypatch) -> None:
    import httpx

    _Client.response = _Response({"rows": [{"id": 1, "name": "A" * 200}]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(option_p0_quality, "_MAX_RESPONSE_BYTES", 32)

    result = await rc.fetch_field_options(
        _api_request(),
        "审批人",
        base_url="https://oa.example",
    )

    assert result["options"] == []
    assert result["source_status"] == "response_too_large"
    assert "响应" in result["note"]
