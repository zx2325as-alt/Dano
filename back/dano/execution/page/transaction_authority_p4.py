"""Publish-time authority for request-captured transaction assets.

Repair operates on an unsealed draft. Before publication the final executable request is
bound to Transaction IR by stable hashes. The final sealed draft is the only P3 draft
allowed to perform a live replay, and every call to the page publisher rechecks the seal.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable
from uuid import UUID

AUTHORITY_VERSION = "transaction-authority/v1"
COMPILER_VERSION = "ir-compiler/p4"
_INSTALLED = False
_DERIVED_TOP_LEVEL = {"skill_interface"}
_REPLAY_PLANS: dict[str, dict] = {}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _ir_without_authority(transaction_ir: dict) -> dict:
    out = copy.deepcopy(transaction_ir)
    out.pop("authority", None)
    return out


def _artifact_view(api_request: dict) -> dict:
    out = copy.deepcopy(api_request)
    out.pop("transaction_ir", None)
    for key in _DERIVED_TOP_LEVEL:
        out.pop(key, None)
    return out


def authority_required(api_request: dict | None) -> bool:
    """P3 request assets are the first assets required to carry a P4 authority seal."""
    apir = api_request or {}
    marker = apir.get("option_reference") or {}
    return bool(
        isinstance(apir.get("transaction_ir"), dict)
        and isinstance(marker, dict)
        and marker.get("version") == "option-reference/v1"
        and marker.get("required")
    )


def seal_api_request(api_request: dict) -> dict:
    """Return a sealed copy; the input object is never mutated."""
    if not isinstance(api_request, dict):
        raise ValueError("api_request must be an object")
    transaction_ir = api_request.get("transaction_ir")
    if not isinstance(transaction_ir, dict):
        raise ValueError("Transaction IR 缺失，不能建立发布权威封印")

    ir_core = _ir_without_authority(transaction_ir)
    from dano.execution.page.transaction_ir import validate_transaction_ir

    ir_issues = validate_transaction_ir(ir_core)
    if ir_issues:
        raise ValueError("Transaction IR 校验失败，不能封印: " + "; ".join(ir_issues))

    artifact = _artifact_view(api_request)
    authority = {
        "version": AUTHORITY_VERSION,
        "compiler_version": COMPILER_VERSION,
        "ir_hash": _hash(ir_core),
        "artifact_hash": _hash(artifact),
        "enforce": True,
    }
    sealed_ir = copy.deepcopy(ir_core)
    sealed_ir["authority"] = authority
    sealed = copy.deepcopy(artifact)
    sealed["transaction_ir"] = sealed_ir

    from dano.execution.page.skill_interface import build_skill_interface

    sealed["skill_interface"] = build_skill_interface(sealed)
    return sealed


def authority_issues(api_request: dict | None) -> list[str]:
    if not isinstance(api_request, dict):
        return ["authority: api_request must be an object"]
    transaction_ir = api_request.get("transaction_ir")
    if not isinstance(transaction_ir, dict):
        return ["authority: transaction_ir missing"]
    authority = transaction_ir.get("authority")
    if not isinstance(authority, dict):
        return ["authority: seal missing"]
    issues: list[str] = []
    if authority.get("version") != AUTHORITY_VERSION:
        issues.append("authority: unsupported seal version")
    if authority.get("compiler_version") != COMPILER_VERSION:
        issues.append("authority: compiler version mismatch")
    if not authority.get("enforce"):
        issues.append("authority: seal is not enforced")
    actual_ir_hash = _hash(_ir_without_authority(transaction_ir))
    if authority.get("ir_hash") != actual_ir_hash:
        issues.append("authority: Transaction IR changed after sealing")
    actual_artifact_hash = _hash(_artifact_view(api_request))
    if authority.get("artifact_hash") != actual_artifact_hash:
        issues.append("authority: compiled api_request changed after sealing")
    return issues


def verify_transaction_authority(api_request: dict | None) -> bool:
    return not authority_issues(api_request)


def publish_authority_issues(asset_type: Any, body: dict | None) -> list[str]:
    """Return hard-gate violations for a draft about to be published."""
    value = getattr(asset_type, "value", asset_type)
    if str(value) != "page_script" or not isinstance(body, dict):
        return []
    api_request = body.get("api_request")
    if not isinstance(api_request, dict) or not authority_required(api_request):
        return []
    return authority_issues(api_request)


def _wrap_self_check(original: Callable):
    def wrapped(api_request: dict, *args, **kwargs):
        issues = list(original(api_request, *args, **kwargs) or [])
        transaction_ir = (api_request or {}).get("transaction_ir") or {}
        authority = transaction_ir.get("authority") if isinstance(transaction_ir, dict) else None
        if isinstance(authority, dict) and authority.get("enforce"):
            for issue in authority_issues(api_request):
                if issue not in issues:
                    issues.append(issue)
        return issues

    return wrapped


def _sealed(api_request: dict | None) -> bool:
    authority = ((api_request or {}).get("transaction_ir") or {}).get("authority")
    return bool(isinstance(authority, dict) and authority.get("enforce"))


def _wrap_sandbox_replay(original: Callable, tools_module):  # noqa: ANN001
    async def wrapped(run_id: str, params: dict):
        draft = None
        try:
            draft = await tools_module._ds.get_draft(UUID(params["asset_draft_id"]))
        except Exception:  # noqa: BLE001
            return await original(run_id, params)
        api_request = ((draft.body or {}).get("api_request") if draft is not None else None)
        if not authority_required(api_request):
            return await original(run_id, params)

        call_params = copy.deepcopy(params)
        if not _sealed(api_request):
            # Remember the requested live plan on the first pre-seal validation, but keep
            # every editable/unreviewed draft dry. Repair rounds cannot overwrite it.
            if run_id not in _REPLAY_PLANS and ("live" in params or "storage_state" in params):
                _REPLAY_PLANS[run_id] = {
                    "live": bool(params.get("live")),
                    "storage_state": copy.deepcopy(params.get("storage_state")),
                    "verify": bool(params.get("verify", False)),
                }
            call_params["live"] = False
            call_params["verify"] = False
            return await original(run_id, call_params)

        # The final sealed draft consumes the remembered plan. In a reversible sandbox it
        # performs the one live write and fact-check; otherwise it remains an honest dry run.
        plan = _REPLAY_PLANS.pop(run_id, None)
        if plan is not None:
            call_params["live"] = bool(plan.get("live"))
            call_params["storage_state"] = copy.deepcopy(plan.get("storage_state"))
            call_params["verify"] = bool(plan.get("verify", False))
        return await original(run_id, call_params)

    return wrapped


def _wrap_publish_asset(original: Callable, tools_module):  # noqa: ANN001
    async def wrapped(run_id: str, params: dict):
        try:
            draft = await tools_module._ds.get_draft(UUID(params["asset_draft_id"]))
        except Exception:  # noqa: BLE001
            draft = None
        if draft is not None:
            issues = publish_authority_issues(draft.asset_type, draft.body)
            if issues:
                tools_module.log.warning(
                    "publish_asset.authority_rejected",
                    draft=str(draft.asset_draft_id),
                    issues=issues,
                )
                return {
                    "published": False,
                    "reason": "Transaction Authority 发布硬闸门未通过: " + "; ".join(issues),
                }
        return await original(run_id, params)

    return wrapped


def install_transaction_authority_p4() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from dano.execution.page import request_capture as rc

    if not getattr(rc.self_check, "__dano_transaction_authority_p4__", False):
        wrapped_check = _wrap_self_check(rc.self_check)
        wrapped_check.__dano_transaction_authority_p4__ = True
        rc.self_check = wrapped_check

    from dano.agent_tools import tools as tools_module

    if not getattr(tools_module.sandbox_replay, "__dano_transaction_authority_p4__", False):
        wrapped_replay = _wrap_sandbox_replay(tools_module.sandbox_replay, tools_module)
        wrapped_replay.__dano_transaction_authority_p4__ = True
        tools_module.sandbox_replay = wrapped_replay
    if not getattr(tools_module.publish_asset, "__dano_transaction_authority_p4__", False):
        wrapped_publish = _wrap_publish_asset(tools_module.publish_asset, tools_module)
        wrapped_publish.__dano_transaction_authority_p4__ = True
        tools_module.publish_asset = wrapped_publish

    _INSTALLED = True
