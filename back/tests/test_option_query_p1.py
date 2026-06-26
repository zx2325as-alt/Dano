from __future__ import annotations

import copy
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


def select_protocol(*, search: bool = True, pagination: bool = True, dependency: bool = True) -> dict:
    protocol: dict = {
        "response": {
            "next_cursor_path": ["data", "nextPage"],
            "has_more_path": ["data", "hasMore"],
            "total_path": ["data", "total"],
        }
    }
    if search:
        protocol["search"] = {
            "location": "json",
            "path": ["keyword"],
            "min_length": 1,
        }
    if pagination:
        protocol["pagination"] = {
            "mode": "page",
            "location": "json",
            "path": ["pageNo"],
            "size_path": ["pageSize"],
            "default_size": 20,
            "max_size": 100,
        }
    if dependency:
        protocol["dependencies"] = [{
            "field": "部门",
            "location": "json",
            "path": ["departmentId"],
            "required": True,
        }]
    return {
        "param": "审批人",
        "source_url": "/api/users/search",
        "source_method": "POST",
        "source_post_data": {
            "keyword": "",
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
        "option_query": protocol,
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
async def test_search_page_and_dependency_are_typed_into_json_body() -> None:
    Client.responses = [Response({
        "data": {
            "rows": [{"id": 12, "name": "张经理"}],
            "nextPage": 3,
            "hasMore": True,
            "total": 81,
        }
    })]
    context = {"部门": 7, "无关字段": "keep"}
    before = copy.deepcopy(context)

    result = await rc.fetch_field_options(
        api_request(),
        "审批人",
        base_url="https://oa.example",
        query=" 张 ",
        cursor=2,
        limit=30,
        context=context,
    )

    assert context == before
    assert Client.calls[0]["json"] == {
        "keyword": "张",
        "pageNo": 2,
        "pageSize": 30,
        "departmentId": 7,
    }
    assert result["options"] == [{"label": "张经理", "value": "12"}]
    assert result["search_supported"] is True
    assert result["depends_on"] == ["部门"]
    assert result["next_cursor"] == 3
    assert result["has_more"] is True
    assert result["total"] == 81
    assert result["pagination_mode"] == "page"


@pytest.mark.asyncio
async def test_missing_dependency_blocks_before_network() -> None:
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


@pytest.mark.asyncio
async def test_short_search_query_blocks_before_network() -> None:
    select = select_protocol(dependency=False)
    select["option_query"]["search"]["min_length"] = 2

    result = await rc.fetch_field_options(
        api_request(select),
        "审批人",
        base_url="https://oa.example",
        query="张",
    )

    assert Client.calls == []
    assert result["source_status"] == "query_too_short"
    assert result["min_query_length"] == 2


@pytest.mark.asyncio
async def test_query_and_form_locations_are_supported_without_dotted_paths() -> None:
    select = select_protocol(pagination=False, dependency=False)
    select["source_url"] = "/api/users"
    select["source_method"] = "GET"
    select["source_post_data"] = None
    select["option_query"]["search"] = {
        "location": "query",
        "path": ["q"],
    }
    Client.responses = [Response({"data": {"rows": [], "hasMore": False}})]

    result = await rc.fetch_field_options(
        api_request(select),
        "审批人",
        base_url="https://oa.example",
        query="alice",
    )

    assert Client.calls[0]["params"] == {"q": "alice"}
    assert result["source_status"] == "empty"


@pytest.mark.asyncio
async def test_invalid_string_path_is_rejected_before_network() -> None:
    select = select_protocol(pagination=False, dependency=False)
    select["option_query"]["search"]["path"] = "keyword"

    result = await rc.fetch_field_options(
        api_request(select),
        "审批人",
        base_url="https://oa.example",
        query="alice",
    )

    assert Client.calls == []
    assert result["source_status"] == "invalid_query_protocol"
    assert "token" in result["note"]


@pytest.mark.asyncio
async def test_page_size_is_clamped_to_protocol_maximum() -> None:
    select = select_protocol(dependency=False)
    select["option_query"]["pagination"]["max_size"] = 25
    Client.responses = [Response({
        "data": {"rows": [], "nextPage": None, "hasMore": False, "total": 0}
    })]

    await rc.fetch_field_options(
        api_request(select),
        "审批人",
        base_url="https://oa.example",
        query="alice",
        limit=999,
    )

    assert Client.calls[0]["json"]["pageSize"] == 25


@pytest.mark.asyncio
async def test_write_validation_searches_by_submitted_label() -> None:
    select = select_protocol(pagination=False, dependency=False)
    Client.responses = [Response({
        "data": {"rows": [{"id": 12, "name": "张经理"}], "hasMore": False}
    })]

    fields, overrides = await rc._resolve_selects(
        api_request(select),
        {"审批人": "张经理"},
        base_url="https://oa.example",
        storage_state=None,
        token_key=None,
        verify=True,
    )

    assert Client.calls[0]["json"]["keyword"] == "张经理"
    assert fields["审批人"] == "张经理"
    assert overrides[("approverId",)] == 12


@pytest.mark.asyncio
async def test_array_validation_searches_each_submitted_value() -> None:
    select = select_protocol(pagination=False, dependency=False)
    select.update({
        "kind": "array",
        "array_path": "approvers",
        "array_tokens": ["approvers"],
        "source_keys": ["id", "name"],
    })
    Client.responses = [
        Response({"data": {"rows": [{"id": 1, "name": "张经理"}]}}),
        Response({"data": {"rows": [{"id": 2, "name": "李经理"}]}}),
    ]

    _fields, overrides = await rc._resolve_selects(
        api_request(select),
        {"审批人": ["张经理", "李经理"]},
        base_url="https://oa.example",
        storage_state=None,
        token_key=None,
        verify=True,
    )

    assert [call["json"]["keyword"] for call in Client.calls] == ["张经理", "李经理"]
    assert overrides[("approvers",)] == [
        {"id": 1, "name": "张经理"},
        {"id": 2, "name": "李经理"},
    ]


@pytest.mark.asyncio
async def test_paged_source_without_search_fails_closed_when_value_is_not_on_first_page() -> None:
    select = select_protocol(search=False, dependency=False)
    Client.responses = [Response({
        "data": {
            "rows": [{"id": 1, "name": "第一页的人"}],
            "nextPage": 2,
            "hasMore": True,
            "total": 80,
        }
    })]

    with pytest.raises(ValueError, match="无法证明"):
        await rc._resolve_selects(
            api_request(select),
            {"审批人": "目标人员"},
            base_url="https://oa.example",
            storage_state=None,
            token_key=None,
            verify=True,
        )
