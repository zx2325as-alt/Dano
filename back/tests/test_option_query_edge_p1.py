from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import dano.execution.page  # noqa: F401
from dano.catalog import manifest
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
    response = Response({"data": {"rows": []}})

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, **kwargs):
        type(self).calls.append({"method": method, "url": url, **kwargs})
        return type(self).response


def _select() -> dict:
    return {
        "param": "审批人",
        "source_url": "/api/users/search",
        "source_method": "POST",
        "source_post_data": {
            "keyword": "",
            "id": None,
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
            "dependencies": [{
                "field": "部门",
                "location": "json",
                "path": ["departmentId"],
                "required": True,
            }],
            "response": {},
        },
    }


def _api_request() -> dict:
    return {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "selects": [_select()],
    }


@pytest.mark.asyncio
async def test_exact_validation_uses_value_from_label_value_object(monkeypatch) -> None:
    import httpx

    Client.calls = []
    Client.response = Response({
        "data": {"rows": [{"id": 12, "name": "张经理"}]}
    })
    monkeypatch.setattr(httpx, "AsyncClient", Client)

    fields, overrides = await rc._resolve_selects(
        _api_request(),
        {"审批人": {"label": "张经理", "value": 12}, "部门": 7},
        base_url="https://oa.example",
        storage_state=None,
        token_key=None,
        verify=True,
    )

    body = Client.calls[0]["json"]
    assert body["id"] == 12
    assert isinstance(body["id"], int)
    assert body["keyword"] == ""
    assert body["departmentId"] == 7
    assert fields["审批人"] == "张经理"
    assert overrides[("approverId",)] == 12


def test_manifest_schema_prop_receives_installed_query_capabilities() -> None:
    skill = SimpleNamespace(field_types={"审批人": "enum"})
    select = _select()

    prop = manifest._schema_prop(skill, "审批人", "审批人", select)

    assert prop["x-options-source"] is True
    assert prop["x-options-search"] is True
    assert prop["x-options-min-query-length"] == 1
    assert prop["x-options-depends-on"] == ["部门"]
    assert prop["x-options-validation"] is True
    assert "source_url" not in prop
    assert "source_post_data" not in prop
