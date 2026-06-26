from __future__ import annotations

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page import request_capture as rc


@pytest.mark.asyncio
async def test_option_source_blocks_cross_origin_before_http(monkeypatch) -> None:
    import httpx

    called = False

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal called
            called = True

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    api_request = {
        "auth_headers": {"Authorization": "Bearer current"},
        "selects": [{
            "param": "审批人",
            "source_url": "https://evil.example/api/users",
            "source_method": "GET",
            "source_records_path": ["rows"],
            "value_key": "id",
            "label_key": "name",
        }],
    }

    result = await rc.fetch_field_options(
        api_request,
        "审批人",
        base_url="https://oa.example",
    )

    assert called is False
    assert result["source_status"] == "cross_origin_blocked"
    assert "同源" in result["note"]


@pytest.mark.asyncio
async def test_option_source_rejects_put_even_when_recorded() -> None:
    api_request = {
        "selects": [{
            "param": "审批人",
            "source_url": "/api/users",
            "source_method": "PUT",
            "source_post_data": "{}",
            "source_content_type": "application/json",
            "source_records_path": ["rows"],
            "value_key": "id",
            "label_key": "name",
        }],
    }

    result = await rc.fetch_field_options(
        api_request,
        "审批人",
        base_url="https://oa.example",
    )

    assert result["source_status"] == "unsafe_method"
    assert "GET 或 POST" in result["note"]


@pytest.mark.asyncio
async def test_option_source_rejects_credentials_embedded_in_url() -> None:
    api_request = {
        "selects": [{
            "param": "审批人",
            "source_url": "https://user:password@oa.example/api/users",
            "source_method": "GET",
            "source_records_path": ["rows"],
            "value_key": "id",
            "label_key": "name",
        }],
    }

    result = await rc.fetch_field_options(
        api_request,
        "审批人",
        base_url="https://oa.example",
    )

    assert result["source_status"] == "credential_in_url"
    assert "用户名或密码" in result["note"]


@pytest.mark.asyncio
async def test_option_source_allows_same_origin_absolute_url(monkeypatch) -> None:
    import httpx

    calls: list[tuple[str, str]] = []

    class Response:
        status_code = 200

        def json(self):
            return {"rows": [{"id": 1, "name": "张经理"}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            calls.append((method, url))
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    api_request = {
        "selects": [{
            "param": "审批人",
            "source_url": "https://oa.example/api/users",
            "source_method": "GET",
            "source_records_path": ["rows"],
            "value_key": "id",
            "label_key": "name",
        }],
    }

    result = await rc.fetch_field_options(
        api_request,
        "审批人",
        base_url="https://oa.example",
    )

    assert result["source_status"] == "ok"
    assert calls == [("GET", "https://oa.example/api/users")]
