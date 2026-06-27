from __future__ import annotations

from types import SimpleNamespace

import pytest

import dano.execution.page  # noqa: F401
import dano.orchestrator  # noqa: F401
from dano.execution.page.option_reference_p3 import REFERENCE_VERSION
from dano.orchestrator.option_reference_store_p3 import (
    MemoryOptionReferenceStore,
    PgOptionReferenceStore,
    set_option_reference_store,
)
from dano.orchestrator.orchestrator import Orchestrator
from dano.shared.enums import Subsystem, TaskState


def api_request_fixture() -> dict:
    return {
        "option_reference": {
            "version": REFERENCE_VERSION,
            "required": True,
            "legacy_raw_values": False,
        },
        "selects": [{
            "param": "审批人",
            "path": "approverId",
            "source_url": "/api/users",
            "source_method": "GET",
            "source_records_path": ["rows"],
            "value_key": "id",
            "label_key": "name",
            "option_reference_required": True,
        }],
    }


class Registry:
    def __init__(self, skill) -> None:
        self.skill = skill

    def by_action(self, subsystem, action):  # noqa: ANN001
        return self.skill if action == "submit_leave" else None


class Store:
    def __init__(self, api_request: dict) -> None:
        self.asset = SimpleNamespace(body={"api_request": api_request})

    async def get(self, asset_id):  # noqa: ANN001
        return self.asset

    async def get_published(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


@pytest.fixture(autouse=True)
def reference_store():
    store = MemoryOptionReferenceStore()
    set_option_reference_store(store)
    yield store
    set_option_reference_store(PgOptionReferenceStore())


def orchestrator_fixture() -> Orchestrator:
    subsystem = Subsystem("A-OA")
    skill = SimpleNamespace(
        page_asset_id="page-asset",
        skill_id="A-OA.submit_leave",
        subsystem=subsystem,
    )
    orchestrator = object.__new__(Orchestrator)
    orchestrator.registry = Registry(skill)
    orchestrator.store = Store(api_request_fixture())
    return orchestrator


@pytest.mark.asyncio
async def test_list_field_options_replaces_raw_value_with_reference(monkeypatch) -> None:
    from dano.execution.page import request_capture as rc
    from dano.execution.page import sessions
    from dano.infra import token_store

    async def fake_fetch(*args, **kwargs):  # noqa: ANN002, ANN003
        return {
            "field": "审批人",
            "options": [{"label": "张经理", "value": 12}],
            "count": 1,
            "submit_mode": "value",
            "source_status": "ok",
        }

    async def no_headers(*args, **kwargs):  # noqa: ANN002, ANN003
        return {}

    monkeypatch.setattr(rc, "fetch_field_options", fake_fetch)
    monkeypatch.setattr(sessions, "session_path_if_exists", lambda *args: None)
    monkeypatch.setattr(token_store, "get_token_headers", no_headers)

    orchestrator = orchestrator_fixture()
    result = await orchestrator.list_field_options(
        Subsystem("A-OA"),
        "submit_leave",
        "审批人",
        tenant="tenant-a",
    )

    token = result["options"][0]["value"]
    assert token.startswith("oref1_")
    assert token != "12"
    assert result["submit_mode"] == "reference"
    assert result["reference_required"] is True


@pytest.mark.asyncio
async def test_raw_dynamic_value_is_blocked_before_original_invoke() -> None:
    orchestrator = orchestrator_fixture()

    result = await orchestrator.invoke_skill(
        Subsystem("A-OA"),
        "submit_leave",
        {"审批人": 12, "原因": "回家"},
        tenant="tenant-a",
    )

    assert result.state == TaskState.NEEDS_INPUT
    assert "必须先查询候选项" in result.message
    assert result.audit["option_reference"]["code"] == "invalid_option_reference"
