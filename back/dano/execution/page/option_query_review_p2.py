"""Review boundary for inferred option-query capabilities.

The browser may approve or reject a server-generated inference. Query implementation
metadata remains server-owned and review decisions reference opaque IDs only.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

_DECISIONS = {"accept", "reject"}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _review_id(select: dict) -> str:
    inference = select.get("option_query_inference") or {}
    material = {
        "path": select.get("path") or select.get("array_path"),
        "source_fingerprint": inference.get("source_fingerprint"),
        "protocol": select.get("option_query") or {},
        "evidence": inference.get("evidence") or [],
    }
    digest = hashlib.sha256(_stable_json(material).encode("utf-8")).hexdigest()[:24]
    return f"oqr_{digest}"


def _is_pending_inference(select: dict) -> bool:
    inference = select.get("option_query_inference")
    return bool(
        isinstance(inference, dict)
        and select.get("option_query")
        and inference.get("status") == "inferred"
        and not inference.get("confirmed_by_user")
    )


def prepare_reviewable_selects(selects: list[dict] | None) -> list[dict]:
    out = copy.deepcopy(list(selects or []))
    for select in out:
        if _is_pending_inference(select):
            select["_option_review_id"] = _review_id(select)
    return out


def _capabilities(select: dict) -> dict:
    protocol = select.get("option_query") or {}
    pagination = protocol.get("pagination") or {}
    dependencies: list[str] = []
    for dependency in protocol.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        field = str(dependency.get("field") or "").strip()
        if field and field not in dependencies:
            dependencies.append(field)
    return {
        "search": bool(protocol.get("search")),
        "pagination": str(pagination.get("mode") or ""),
        "validation": bool(protocol.get("validation")),
        "dependencies": dependencies,
    }


def _evidence_count(inference: dict) -> int:
    refs: set[str] = set()
    for item in inference.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        for ref in item.get("evidence_refs") or []:
            value = str(ref or "")
            if value:
                refs.add(value)
    return len(refs)


def public_selects(selects: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for select in selects or []:
        inference = select.get("option_query_inference") or {}
        public = {
            "path": select.get("path") or select.get("array_path") or "",
            "label": select.get("label") or "",
            "count": int(select.get("count") or len(select.get("options") or [])),
            "kind": select.get("kind") or "single",
            "capabilities": _capabilities(select),
        }
        if inference:
            public["inference"] = {
                "status": inference.get("status") or "",
                "confidence": inference.get("confidence"),
                "confirmed_by_user": bool(inference.get("confirmed_by_user")),
                "evidence_count": _evidence_count(inference),
                "review_id": select.get("_option_review_id"),
            }
        out.append(public)
    return out


def public_transaction_ir(ir: dict | None) -> dict | None:
    if not isinstance(ir, dict):
        return None
    capture = ir.get("capture") or {}
    return {
        "version": ir.get("version"),
        "capture": {
            "capture_hash": capture.get("capture_hash"),
            "trace_hash": capture.get("trace_hash"),
        },
        "input_count": len(ir.get("inputs") or []),
        "source_count": len(ir.get("sources") or []),
        "binding_count": len(ir.get("bindings") or []),
    }


def _parse_decisions(decisions: Any) -> dict[str, str]:
    if decisions in (None, []):
        return {}
    if not isinstance(decisions, list):
        raise ValueError("option_query_decisions 必须是数组")
    out: dict[str, str] = {}
    for index, item in enumerate(decisions):
        if not isinstance(item, dict):
            raise ValueError(f"option_query_decisions[{index}] 必须是对象")
        review_id = str(item.get("review_id") or "").strip()
        decision = str(item.get("decision") or "").strip().lower()
        if not review_id:
            raise ValueError(f"option_query_decisions[{index}].review_id 缺失")
        if decision not in _DECISIONS:
            raise ValueError(f"option_query_decisions[{index}].decision 仅支持 accept/reject")
        if review_id in out:
            raise ValueError(f"重复的 option review decision: {review_id}")
        out[review_id] = decision
    return out


def apply_option_review_decisions(server_selects: list[dict] | None, decisions: Any) -> list[dict]:
    selects = prepare_reviewable_selects(server_selects)
    parsed = _parse_decisions(decisions)
    pending: dict[str, dict] = {
        str(select.get("_option_review_id")): select
        for select in selects
        if select.get("_option_review_id")
    }
    unknown = sorted(set(parsed) - set(pending))
    if unknown:
        raise ValueError(f"未知或已过期的 option review: {', '.join(unknown)}")
    unresolved = sorted(set(pending) - set(parsed))
    if unresolved:
        raise ValueError(f"还有 {len(unresolved)} 条候选查询能力需要确认")

    for review_id, select in pending.items():
        decision = parsed[review_id]
        if decision == "accept":
            inference = copy.deepcopy(select.get("option_query_inference") or {})
            inference["status"] = "confirmed"
            inference["confirmed_by_user"] = True
            inference["review_id"] = review_id
            select["option_query_inference"] = inference
        else:
            select.pop("option_query", None)
            select.pop("option_query_inference", None)
        select.pop("_option_review_id", None)

    for select in selects:
        select.pop("_option_review_id", None)
    return selects


def trusted_identity(identity: list[dict] | None) -> list[dict]:
    return copy.deepcopy(list(identity or []))
