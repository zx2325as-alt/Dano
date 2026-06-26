from __future__ import annotations

import json

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page import option_query_p1 as p1
from dano.execution.page import request_capture as rc


class Response:
    status_code = 200

    def __init__(self, data) -> None:
        self.data = data
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        return self.data


class Client:
    calls: list[dict] = []
    response = Response({})

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, **kwargs):
        type(self).calls.append({"method": method, "url": url, **kwargs})
        return type(self).response


def make_select(options=None) -> dict:
    return {
        "param": "审批人",
        "source_url": "/api/users/search",
        "source_method": "POST",
        "source_post_data": {"keyword": ""},
        "source_content_type": "application/json",
        "source_records_path": ["data", "rows"],
        "label_key": "name",
        "value_key": "id",
        "id_path": "approverId",
        "id_tokens": ["approverId"],
        "options": options or [{"label": "张经理", "value": "12"}],
        "option_query": {
            "search": {
                "location": "json",
                "path": ["keyword"],
                "min_length": 1,
            }
        },
    }


@pytest.fixture(autouse=True)
def reset_client(monkeypatch):
    import httpx

    Client.calls = []
    Client.response = Response({})
    monkeypatch.setattr(httpx, "AsyncClient", Client)


@pytest.mark.asyncio
async def test_numeric_id_searches_live_source_by_unique_recorded_label() -> None:
    Client.response = Response({
        "data": {"rows": [{"id": 12, "name": "张经理"}]}
    })
    api_request = {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "selects": [make_select()],
    }

    fields, overrides = await rc._resolve_selects(
        api_request,
        {"审批人": 12},
        base_url="https://oa.example",
        storage_state=None,
        token_key=None,
        verify=True,
    )

    assert Client.calls[0]["json"]["keyword"] == "张经理"
    assert fields["审批人"] == "张经理"
    assert overrides[("approverId",)] == 12


@pytest.mark.asyncio
async def test_live_result_must_still_contain_submitted_stable_value() -> None:
    Client.response = Response({
        "data": {"rows": [{"id": 99, "name": "张经理"}]}
    })
    api_request = {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "selects": [make_select()],
    }

    with pytest.raises(ValueError, match="不在当前候选项"):
        await rc._resolve_selects(
            api_request,
            {"审批人": 12},
            base_url="https://oa.example",
            storage_state=None,
            token_key=None,
            verify=True,
        )

    assert Client.calls[0]["json"]["keyword"] == "张经理"


def test_ambiguous_snapshot_keeps_original_search_value() -> None:
    select = make_select([
        {"label": "张经理", "value": "12"},
        {"label": "张经理（旧）", "value": "12"},
    ])

    prepared = p1._prepare_select(
        select,
        query="12",
        validation=True,
    )

    assert prepared["source_post_data"]["keyword"] == "12"
    assert prepared["_option_query_runtime"].get("validation_strategy") is None
