from __future__ import annotations

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest

import dano.execution.page  # noqa: F401
from dano.execution.page.transaction_authority_p4 import (
    COMPILER_VERSION,
    _REPLAY_PLANS,
    _wrap_publish_asset,
    _wrap_sandbox_replay,
    _wrap_self_check,
    authority_issues,
    publish_authority_issues,
    seal_api_request,
    verify_transaction_authority,
)
from dano.shared.enums import AssetType


def api_request_fixture() -> dict:
    return {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "path": "/api/leave/submit",
        "params": ["原因"],
        "field_types": {"原因": "string"},
        "body_template": {"reason": "{{原因}}"},
        "option_reference": {
            "version": "option-reference/v1",
            "required": True,
            "legacy_raw_values": False,
        },
        "transaction_ir": {
            "version": "transaction-ir/v1",
            "method": "POST",
            "url": "https://oa.example/api/leave/submit",
            "path": "/api/leave/submit",
            "inputs": [{"name": "原因", "path": "reason"}],
            "bindings": [{"input": "原因", "target_path": "reason", "mode": "direct"}],
            "capture": {"capture_hash": "capture-a", "trace_hash": "trace-a"},
        },
    }


def test_seal_binds_ir_and_executable_artifact_without_mutating_input() -> None:
    original = api_request_fixture()
    sealed = seal_api_request(original)

    assert "authority" not in original["transaction_ir"]
    authority = sealed["transaction_ir"]["authority"]
    assert authority["compiler_version"] == COMPILER_VERSION
    assert authority["enforce"] is True
    assert verify_transaction_authority(sealed) is True
    assert authority_issues(sealed) == []
    assert sealed["skill_interface"]["provenance"]["capture_hash"] == "capture-a"


def test_ir_artifact_and_compiler_tampering_are_detected() -> None:
    sealed = seal_api_request(api_request_fixture())

    ir_changed = copy.deepcopy(sealed)
    ir_changed["transaction_ir"]["inputs"][0]["name"] = "伪造字段"
    assert "authority: Transaction IR changed after sealing" in authority_issues(ir_changed)

    artifact_changed = copy.deepcopy(sealed)
    artifact_changed["body_template"]["reason"] = "tampered"
    assert "authority: compiled api_request changed after sealing" in authority_issues(artifact_changed)

    compiler_changed = copy.deepcopy(sealed)
    compiler_changed["transaction_ir"]["authority"]["compiler_version"] = "other"
    assert "authority: compiler version mismatch" in authority_issues(compiler_changed)


def test_self_check_rejects_disabled_or_malformed_existing_seal() -> None:
    sealed = seal_api_request(api_request_fixture())
    sealed["transaction_ir"]["authority"]["enforce"] = False
    wrapped = _wrap_self_check(lambda _api_request: [])

    issues = wrapped(sealed)

    assert "authority: seal is not enforced" in issues


def test_invalid_transaction_ir_cannot_be_sealed() -> None:
    request = api_request_fixture()
    request["transaction_ir"]["version"] = "invalid"

    with pytest.raises(ValueError, match="Transaction IR 校验失败"):
        seal_api_request(request)


def test_publish_gate_requires_seal_only_for_p3_page_assets() -> None:
    unsealed = api_request_fixture()
    assert publish_authority_issues(AssetType.PAGE_SCRIPT, {"api_request": unsealed}) == [
        "authority: seal missing"
    ]

    missing_ir = copy.deepcopy(unsealed)
    missing_ir.pop("transaction_ir")
    assert publish_authority_issues(AssetType.PAGE_SCRIPT, {"api_request": missing_ir}) == [
        "authority: transaction_ir missing"
    ]

    sealed = seal_api_request(unsealed)
    assert publish_authority_issues(AssetType.PAGE_SCRIPT, {"api_request": sealed}) == []
    assert publish_authority_issues(AssetType.CONNECTOR, {"api_request": unsealed}) == []

    legacy = copy.deepcopy(unsealed)
    legacy.pop("option_reference")
    assert publish_authority_issues(AssetType.PAGE_SCRIPT, {"api_request": legacy}) == []


class FakeDraftStore:
    def __init__(self, draft) -> None:
        self.draft = draft

    async def get_draft(self, _draft_id):
        return self.draft


class FakeLog:
    def __init__(self) -> None:
        self.events = []

    def warning(self, event, **kwargs):
        self.events.append((event, kwargs))


@pytest.mark.asyncio
async def test_preseal_replays_are_dry_and_final_sealed_replay_consumes_live_plan() -> None:
    run_id = "authority-replay-test"
    _REPLAY_PLANS.pop(run_id, None)
    draft = SimpleNamespace(
        asset_draft_id=uuid4(),
        body={"api_request": api_request_fixture()},
    )
    tools = SimpleNamespace(_ds=FakeDraftStore(draft))
    calls = []

    async def original(_run_id, params):
        calls.append(copy.deepcopy(params))
        return {"passed": True, "mode": "live" if params.get("live") else "dry"}

    wrapped = _wrap_sandbox_replay(original, tools)
    draft_id = str(draft.asset_draft_id)
    storage_state = {"cookies": [{"name": "session", "value": "test"}]}

    first = await wrapped(run_id, {
        "asset_draft_id": draft_id,
        "sample_inputs": {"原因": "测试"},
        "live": True,
        "storage_state": storage_state,
        "verify": False,
    })
    assert first["mode"] == "dry"
    assert calls[-1]["live"] is False

    # Repair/review can run more deterministic checks but cannot overwrite the original
    # final live plan or issue an editable write.
    await wrapped(run_id, {"asset_draft_id": draft_id, "sample_inputs": {"原因": "测试"}})
    assert calls[-1]["live"] is False
    assert _REPLAY_PLANS[run_id]["live"] is True

    draft.body = {"api_request": seal_api_request(api_request_fixture())}
    final = await wrapped(run_id, {
        "asset_draft_id": draft_id,
        "sample_inputs": {"原因": "测试"},
        "live": False,
    })
    assert final["mode"] == "live"
    assert calls[-1]["live"] is True
    assert calls[-1]["storage_state"] == storage_state
    assert run_id not in _REPLAY_PLANS


@pytest.mark.asyncio
async def test_publish_callable_rejects_unsealed_p3_before_repository_publish() -> None:
    draft = SimpleNamespace(
        asset_draft_id=uuid4(),
        asset_type=AssetType.PAGE_SCRIPT,
        body={"api_request": api_request_fixture()},
    )
    tools = SimpleNamespace(_ds=FakeDraftStore(draft), log=FakeLog())
    original_calls = []

    async def original(run_id, params):
        original_calls.append((run_id, params))
        return {"published": True}

    wrapped = _wrap_publish_asset(original, tools)
    result = await wrapped("publish-test", {"asset_draft_id": str(draft.asset_draft_id)})

    assert result["published"] is False
    assert "authority: seal missing" in result["reason"]
    assert original_calls == []
    assert tools.log.events[0][0] == "publish_asset.authority_rejected"

    draft.body = {"api_request": seal_api_request(api_request_fixture())}
    result = await wrapped("publish-test", {"asset_draft_id": str(draft.asset_draft_id)})
    assert result["published"] is True
    assert len(original_calls) == 1


def test_runtime_entrypoints_are_wrapped() -> None:
    from dano.agent_tools import tools

    assert getattr(tools.sandbox_replay, "__dano_transaction_authority_p4__", False) is True
    assert getattr(tools.publish_asset, "__dano_transaction_authority_p4__", False) is True
