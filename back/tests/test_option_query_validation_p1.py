from __future__ import annotations

import json

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page import request_capture as rc


class Response:
    def __init__(self, data, status_code: int = 200) -> None:
        self.data = data
        self.status_code = status_code
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        return self.data


class Client:
    responses: list[Response] = []
    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, **kwargs):
        type(self).calls.append({"method": method, "url": url, **kwargs})
        if not type(self).responses:
            raise AssertionError("unexpected HTTP request")
        return type(self).responses.pop(0)


def select_protocol() -> dict:
    return {
        "param": "审批人",
        "source_url": "/api/users/search",
        "source_method": "POST",
        "source_post_data": {
            "keyword": "",
            "id": None,
            "pageNo": 1,
            "pageSize": 20,
            "departmentId": None,
        },
        "source_content_type": "application/json",
        "source_records_path": ["data", "rows"],
        "label_key": "name",
        "value_key": "id",
        "id_path": "approverId",
        "id_tokens": ["approverId"],
        "option_query": {
            "search": {
                "location": "json",
                "path": ["keyword"],
                "min_length": 1,
            },
            "validation": {
                "location": "json",
                "path": ["id"],
            },
            "pagination": {
                "mode": "page",
                "location": "json",
                "path": ["pageNo"],
                "size_path": ["pageSize"],
                "default_size": 20,
                "max_size": 100,
            },
            "dependencies": [{
                "field": "部门",
                "location": "json",
                "path": ["departmentId"],
                "required": True,
            }],
            "response": {
                "next_cursor_path": ["data", "nextPage"],
                "has_more_path": ["data", "hasMore"],
                "total_path": ["data", "total"],
            },
        },
    }


def api_request(select: dict | None = None) -> dict:
    return {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "selects": [select or select_protocol()],
    }


@pytest.fixture(autouse=True)
def reset_client(monkeypatch):
    import httpx

    Client.responses = []
    Client.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", Client)


@pytest.mark.asyncio
async def test_waiting_response_still_exposes_query_capabilities() -> None:
    result = await rc.fetch_field_options(
        api_request(),
        "审批人",
        base_url="https://oa.example",
        query="张",
        context={},
    )

    assert Client.calls == []
    assert result["source_status"] == "missing_dependency"
    assert result["missing_dependencies"] == ["部门"]
    assert result["search_supported"] is True
    assert result["validation_supported"] is True
    assert result["depends_on"] == ["部门"]
    assert result["pagination_mode"] == "page"
    assert result["min_query_length"] == 1


@pytest.mark.asyncio
async def test_dependency_context_encoded_size_is_bounded() -> None:
    result = await rc.fetch_field_options(
        api_request(),
        "审批人",
        base_url="https://oa.example",
        query="张",
        context={"部门": 7, "填充": "x" * (70 * 1024)},
    )

    assert Client.calls == []
    assert result["source_status"] == "invalid_context"
    assert "安全上限" in result["note"]


@pytest.mark.asyncio
async def test_cursor_protocol_requires_explicit_next_cursor_path() -> None:
    select = select_protocol()
    select["option_query"]["pagination"]["mode"] = "cursor"
    del select["option_query"]["response"]["next_cursor_path"]

    result = await rc.fetch_field_options(
        api_request(select),
        "审批人",
        base_url="https://oa.example",
        query="张",
        context={"部门": 7},
    )

    assert Client.calls == []
    assert result["source_status"] == "invalid_query_protocol"
    assert "next_cursor_path" in result["note"]


@pytest.mark.asyncio
async def test_write_validation_uses_exact_value_binding_not_name_search() -> None:
    Client.responses = [Response({
        "data": {
            "rows": [{"id": 12, "name": "张经理"}],
            "nextPage": None,
            "hasMore": False,
            "total": 1,
        }
    })]

    fields, overrides = await rc._resolve_selects(
        api_request(),
        {"审批人": 12, "部门": 7},
        base_url="https://oa.example",
        storage_state=None,
        token_key=None,
        verify=True,
    )

    assert Client.calls[0]["json"]["id"] == 12
    assert Client.calls[0]["json"]["keyword"] == ""
    assert Client.calls[0]["json"]["departmentId"] == 7
    assert fields["审批人"] == "张经理"
    assert overrides[("approverId",)] == 12


@pytest.mark.asyncio
async def test_non_paginated_source_does_not_synthesize_page_metadata() -> None:
    select = select_protocol()
    del select["option_query"]["pagination"]
    Client.responses = [Response({
        "data": {"rows": [{"id": 12, "name": "张经理"}]}
    })]

    result = await rc.fetch_field_options(
        api_request(select),
        "审批人",
        base_url="https://oa.example",
        query="张",
        context={"部门": 7},
    )

    assert result["source_status"] == "ok"
    assert result["pagination_mode"] is None
    assert "next_cursor" not in result
    assert "has_more" not in result
