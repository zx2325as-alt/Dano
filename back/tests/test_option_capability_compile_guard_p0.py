from __future__ import annotations

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page import request_capture as rc


def _request() -> dict:
    return {
        "method": "POST",
        "url": "https://oa.example/api/submit",
        "post_data": '{"approverId":12}',
        "content_type": "application/json",
        "headers": {},
    }


def test_suggest_selects_scrubs_sensitive_query_and_url_credentials() -> None:
    reads = [{
        "method": "GET",
        "url": "https://user:password@oa.example/api/users?tenant=7&accessToken=secret-value",
        "records_path": ["rows"],
        "json": {"rows": [{"id": 12, "name": "张经理"}]},
    }]

    selects = rc.suggest_selects('{"approverId":12}', reads)

    assert len(selects) == 1
    select = selects[0]
    assert "user:password" not in select["source_url"]
    assert "secret-value" not in select["source_url"]
    assert "__DANO_REDACTED__" in select["source_url"]
    assert select["source_url_had_credentials"] is True
    assert select["source_sensitive_query_keys"] == ["accessToken"]


def test_manual_select_metadata_is_scrubbed_during_compile() -> None:
    select = {
        "path": "approverId",
        "tokens": ["approverId"],
        "source_url": "https://oa.example/api/users?api_key=url-secret",
        "source_method": "POST",
        "source_post_data": '{"departmentId":7,"password":"body-secret"}',
        "source_content_type": "application/json",
        "source_records_path": ["rows"],
        "label_key": "name",
        "value_key": "id",
    }

    compiled = rc.build_api_request(_request(), {"approverId": "审批人"}, selects=[select])

    assert compiled is not None
    compiled_select = compiled["selects"][0]
    assert "url-secret" not in compiled_select["source_url"]
    assert "body-secret" not in compiled_select["source_post_data"]
    assert compiled_select["source_sensitive_query_keys"] == ["api_key"]
    assert compiled_select["source_sensitive_body_keys"] == ["password"]
    assert compiled_select["source_target_origin"] == "https://oa.example"


@pytest.mark.asyncio
async def test_scrubbed_sensitive_query_source_is_not_replayed(monkeypatch) -> None:
    import httpx

    called = False

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal called
            called = True

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    api_request = {
        "url": "https://oa.example/api/submit",
        "selects": [{
            "param": "审批人",
            "source_url": "https://oa.example/api/users?accessToken=__DANO_REDACTED__",
            "source_method": "GET",
            "source_sensitive_query_keys": ["accessToken"],
            "source_records_path": ["rows"],
            "label_key": "name",
            "value_key": "id",
        }],
    }

    result = await rc.fetch_field_options(api_request, "审批人")

    assert called is False
    assert result["source_status"] == "sensitive_request"
    assert "查询参数" in result["note"]


@pytest.mark.asyncio
async def test_scrubbed_basic_auth_source_is_not_replayed(monkeypatch) -> None:
    import httpx

    called = False

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal called
            called = True

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    api_request = {
        "url": "https://oa.example/api/submit",
        "selects": [{
            "param": "审批人",
            "source_url": "https://oa.example/api/users",
            "source_method": "GET",
            "source_url_had_credentials": True,
            "source_records_path": ["rows"],
            "label_key": "name",
            "value_key": "id",
        }],
    }

    result = await rc.fetch_field_options(api_request, "审批人")

    assert called is False
    assert result["source_status"] == "credential_in_url"
