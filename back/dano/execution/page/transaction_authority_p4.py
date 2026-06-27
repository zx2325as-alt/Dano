"""Publish-time authority seal for request-captured transaction assets.

Inference and repair may operate on an unsealed draft. Immediately before publication the
final executable request is deterministically bound to its Transaction IR. The seal covers
both the private IR semantics and the complete executable artifact (excluding only derived
public projections). Any post-seal mutation invalidates the asset and is rejected by
self-check and the lowest-level publish gate.

This is the migration boundary before a fully direct IR compiler: legacy builders may
still bootstrap a draft, but the published artifact is immutable and hash-bound to the IR.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable

AUTHORITY_VERSION = "transaction-authority/v1"
COMPILER_VERSION = "ir-compiler/p4"
_INSTALLED = False
_DERIVED_TOP_LEVEL = {"skill_interface"}


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

    # Public interface is derived after sealing and intentionally excluded from the seal;
    # it can always be regenerated without changing executable semantics.
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
    """Return hard-gate violations for a draft about to be published.

    Legacy and non-page assets remain compatible. A P3 page asset is publishable only when
    its full server-owned api_request carries a valid P4 seal.
    """
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
        # Drafts are intentionally unsealed while repair is still allowed. Once a seal is
        # present it is immutable and every deterministic replay verifies it.
        if isinstance(authority, dict) and authority.get("enforce"):
            for issue in authority_issues(api_request):
                if issue not in issues:
                    issues.append(issue)
        return issues

    return wrapped


def install_transaction_authority_p4() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from dano.execution.page import request_capture as rc

    if not getattr(rc.self_check, "__dano_transaction_authority_p4__", False):
        wrapped = _wrap_self_check(rc.self_check)
        wrapped.__dano_transaction_authority_p4__ = True
        rc.self_check = wrapped
    _INSTALLED = True
