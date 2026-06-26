from __future__ import annotations

import json

import pytest

# Importing the package installs the additive P0 compatibility layer.
import dano.execution.page  # noqa: F401
from dano.execution.page import request_capture as rc


def test_suggest_selects_preserves_recorded_source_method_and_body() -> None:
    submit = '{"approverId":12}'
    reads = [{
        "method": "POST",
        "url": "https://oa.example/api/user/search",
        "post_data": '{"deptId":7,"keyword":""}',
        "content_type": "application/json",
        "json": {"rows": [{"userId": 12, "nickName": "张经理"}]},
    }]

    out = rc.suggest_selects(submit, reads)

    assert len(out) == 1
    assert out[0]["source_method"] == "POST"
    assert json.loads(out[0]["source_post_data"]) == {"deptId": 7, "keyword": ""}
    assert out[0]["source_content_type"] == "application/json"


class _Response:
    def __init__(self, status_code: int, data) -> None:
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        return self._data


class _Client:
    calls: list[dict] = []
    response = _Response(200, {"rows": [{"id": 1, "name": "会议室A"}]})

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, headers=None, **kwargs):
        type(self).calls.append({"method": method, "url": url, "headers": headers, **kwargs})
        return type(self).response


@pytest.mark.asyncio
async def test_fetch_field_options_replays_post_json(monkeypatch) -> None:
    import httpx

    _Client.calls = []
    _Client.response = _Response(200, {"rows": [{"id": 1, "name": "会议室A"}]})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    api_request = {
        "selects": [{
            "param": "会议室",
            "source_url": "/api/room/search",
            "source_method": "POST",
            "source_post_data": '{"date":"2026-06-26"}',
            "source_content_type": "application/json",
            "value_key": "id",
            "label_key": "name",
        }]
    }

    out = await rc.fetch_field_options(api_request, "会议室", base_url="https://oa.example")

    assert out["source_status"] == "ok"
    assert out["options"] == [{"label": "会议室A", "value": 1}]
    assert _Client.calls[0]["method"] == "POST"
    assert _Client.calls[0]["json"] == {"date": "2026-06-26"}


@pytest.mark.asyncio
async def test_fetch_field_options_reports_auth_expired_without_snapshot_fallback(monkeypatch) -> None:
    import httpx

    _Client.calls = []
    _Client.response = _Response(401, {"message": "expired"})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    api_request = {
        "selects": [{
            "param": "审批人",
            "source_url": "/api/user/search",
            "source_method": "POST",
            "source_post_data": "{}",
            "source_content_type": "application/json",
            "value_key": "id",
            "label_key": "name",
        }]
    }

    out = await rc.fetch_field_options(api_request, "审批人", base_url="https://oa.example")

    assert out["options"] == []
    assert out["source_status"] == "auth_expired"
    assert out["http_status"] == 401
    assert "登录态" in out["note"]


@pytest.mark.asyncio
async def test_execute_select_fails_closed_when_source_unavailable(monkeypatch) -> None:
    import httpx

    _Client.response = _Response(403, {"message": "forbidden"})
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    api_request = {
        "method": "POST",
        "url": "https://oa.example/api/submit",
        "body_template": {"approverId": "{{审批人}}"},
        "params": ["审批人"],
        "selects": [{
            "param": "审批人",
            "source_url": "/api/user/search",
            "source_method": "POST",
            "source_post_data": "{}",
            "source_content_type": "application/json",
            "value_key": "id",
            "label_key": "name",
        }],
    }

    out = await rc.execute_api_request(api_request, {"审批人": 12}, send=True)

    assert out["ok"] is False
    assert "没有读取候选项的权限" in out["detail"]
