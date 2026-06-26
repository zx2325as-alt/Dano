from __future__ import annotations

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page import option_p0
from dano.execution.page.option_query import query_field_options


@pytest.mark.asyncio
async def test_upstream_pagination_does_not_apply_offset_twice(monkeypatch) -> None:
    captured: list[dict] = []

    async def fake_fetch(select, **kwargs):
        captured.append(select)
        offset = int(select["source_query"]["offset"])
        limit = int(select["source_query"]["limit"])
        rows = [
            {"id": offset + index, "name": f"候选{offset + index}"}
            for index in range(limit)
        ]
        return rows, {"ok": True, "status": 200, "source_status": "ok", "message": ""}

    monkeypatch.setattr(option_p0, "_fetch_options", fake_fetch)
    api_request = {
        "selects": [{
            "param": "审批人",
            "source_url": "/api/users",
            "source_query": {"offset": 0, "limit": 0},
            "value_key": "id",
            "label_key": "name",
            "source_input_bindings": [
                {"from": "offset", "target": "query", "tokens": ["offset"]},
                {"from": "limit", "target": "query", "tokens": ["limit"]},
            ],
        }],
    }

    first = await query_field_options(api_request, "审批人", limit=2)
    assert first["options"] == [
        {"label": "候选0", "value": "0"},
        {"label": "候选1", "value": "1"},
    ]
    assert first["has_more"] is True

    second = await query_field_options(
        api_request, "审批人", limit=2, cursor=first["next_cursor"])
    assert captured[-1]["source_query"] == {"offset": 2, "limit": 2}
    assert second["options"] == [
        {"label": "候选2", "value": "2"},
        {"label": "候选3", "value": "3"},
    ]
