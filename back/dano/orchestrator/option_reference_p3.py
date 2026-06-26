"""Issue and redeem opaque option references around Orchestrator calls.

The browser sees a short-lived random reference instead of a target-system ID. The
reference is bound to tenant, skill, field, source contract and dependency context.
Redemption happens before the existing P1 live validation, so a valid reference is not
itself proof that the target option still exists.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from typing import Any, Callable
from uuid import uuid4

from dano.execution.page.option_reference_p3 import (
    dynamic_selects,
    reference_required,
    source_fingerprint,
)
from dano.orchestrator.option_reference_store_p3 import (
    OptionReferenceError,
    OptionReferenceRecord,
    OptionReferenceScopeMismatch,
    get_option_reference_store,
    looks_like_reference,
    reference_ttl_seconds,
)

_INSTALLED = False


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _skill_id(subsystem, action: str) -> str:  # noqa: ANN001
    return f"{subsystem.value}.{action}"


def _dependency_fields(select: dict | None) -> list[str]:
    protocol = (select or {}).get("option_query") or {}
    out: list[str] = []
    for dependency in protocol.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        field = str(dependency.get("field") or "").strip()
        if field and field not in out:
            out.append(field)
    return out


def _context_hash(select: dict | None, context: dict | None) -> str:
    fields = _dependency_fields(select)
    if not fields:
        return ""
    context = context or {}
    material = {field: copy.deepcopy(context.get(field)) for field in fields}
    return hashlib.sha256(_stable_json(material).encode("utf-8")).hexdigest()


def _token_of(value: Any) -> str | None:
    if looks_like_reference(value):
        return value
    if isinstance(value, dict) and looks_like_reference(value.get("value")):
        return value.get("value")
    return None


def _select_map(api_request: dict | None) -> dict[str, dict]:
    return {
        str(select.get("param")): select
        for select in dynamic_selects(api_request)
        if select.get("param")
    }


async def _api_request_for(orchestrator, subsystem, action: str) -> tuple[object | None, dict | None]:  # noqa: ANN001
    skill = orchestrator.registry.by_action(subsystem, action)
    if skill is None or not getattr(skill, "page_asset_id", None):
        return skill, None
    env = await orchestrator.store.get(skill.page_asset_id)
    api_request = (env.body or {}).get("api_request") if env else None
    return skill, copy.deepcopy(api_request) if isinstance(api_request, dict) else None


def _scope_error(record: OptionReferenceRecord, *, tenant: str, skill_id: str,
                 field: str, fingerprint: str) -> None:
    if record.tenant != tenant:
        raise OptionReferenceScopeMismatch("候选引用不属于当前租户")
    if record.skill_id != skill_id:
        raise OptionReferenceScopeMismatch("候选引用不属于当前 Skill")
    if record.field != field:
        raise OptionReferenceScopeMismatch("候选引用不属于当前字段")
    if record.source_fingerprint != fingerprint:
        raise OptionReferenceScopeMismatch("候选来源已变化，请重新查询候选项")


async def _decode_present_references(
    api_request: dict,
    fields: dict | None,
    *,
    tenant: str,
    skill_id: str,
    require_all_dynamic_values: bool,
) -> tuple[dict, list[tuple[OptionReferenceRecord, dict]]]:
    decoded = copy.deepcopy(fields or {})
    select_by_field = _select_map(api_request)
    redeemed: list[tuple[OptionReferenceRecord, dict]] = []
    store = get_option_reference_store()

    for field, select in select_by_field.items():
        if field not in decoded:
            continue
        submitted = decoded[field]
        values = submitted if (select.get("kind") == "array" and isinstance(submitted, list)) else [submitted]
        decoded_values: list[Any] = []
        for value in values:
            token = _token_of(value)
            if token is None:
                if require_all_dynamic_values:
                    raise OptionReferenceError(f"字段 {field} 必须先查询候选项并提交返回的候选引用")
                decoded_values.append(value)
                continue
            record = await store.redeem(token)
            _scope_error(
                record,
                tenant=tenant,
                skill_id=skill_id,
                field=field,
                fingerprint=source_fingerprint(select),
            )
            redeemed.append((record, select))
            decoded_values.append(copy.deepcopy(record.value))
        decoded[field] = decoded_values if select.get("kind") == "array" else decoded_values[0]

    # Dependency binding is checked only after all referenced fields are decoded.
    for record, select in redeemed:
        if record.context_hash != _context_hash(select, decoded):
            raise OptionReferenceScopeMismatch("候选引用的级联依赖已变化，请重新查询候选项")
    return decoded, redeemed


def _error_result(field: str, exc: OptionReferenceError) -> dict:
    return {
        "field": field,
        "options": [],
        "count": 0,
        "source_status": getattr(exc, "code", "invalid_option_reference"),
        "note": str(exc),
        "reference_required": True,
    }


async def _issue_option_references(
    result: dict,
    *,
    tenant: str,
    skill_id: str,
    field: str,
    select: dict,
    context: dict,
) -> dict:
    options = result.get("options") or []
    if not isinstance(options, list) or not options:
        return result
    expires_at = time.time() + reference_ttl_seconds()
    fingerprint = source_fingerprint(select)
    context_digest = _context_hash(select, context)
    store = get_option_reference_store()
    public_options: list[dict] = []
    for option in options:
        if not isinstance(option, dict) or "value" not in option:
            continue
        label = str(option.get("label") or "")
        token = await store.issue(OptionReferenceRecord(
            tenant=tenant,
            skill_id=skill_id,
            field=field,
            source_fingerprint=fingerprint,
            value=copy.deepcopy(option.get("value")),
            label=label,
            context_hash=context_digest,
            expires_at=expires_at,
        ))
        public_options.append({"label": label, "value": token})
    out = copy.deepcopy(result)
    out["options"] = public_options
    out["count"] = len(public_options)
    out["submit_mode"] = "reference[]" if select.get("kind") == "array" else "reference"
    out["reference_required"] = True
    out["reference_version"] = "option-reference/v1"
    out["reference_expires_at"] = int(expires_at)
    return out


def install_option_reference_broker_p3() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.orchestrator.orchestrator import Orchestrator
    from dano.orchestrator.types import TaskOutcome
    from dano.shared.enums import TaskState

    original_list = Orchestrator.list_field_options
    original_invoke = Orchestrator.invoke_skill

    async def list_field_options_with_references(
        self,
        subsystem,
        action: str,
        field: str,
        *,
        tenant: str = "",
        query: str | None = None,
        cursor: str | int | None = None,
        limit: int = 50,
        context: dict | None = None,
    ) -> dict:
        _skill, api_request = await _api_request_for(self, subsystem, action)
        if not reference_required(api_request):
            return await original_list(
                self, subsystem, action, field,
                tenant=tenant, query=query, cursor=cursor, limit=limit, context=context,
            )
        select = _select_map(api_request).get(field)
        if select is None:
            return await original_list(
                self, subsystem, action, field,
                tenant=tenant, query=query, cursor=cursor, limit=limit, context=context,
            )
        skill_id = _skill_id(subsystem, action)
        try:
            decoded_context, _ = await _decode_present_references(
                api_request,
                context or {},
                tenant=tenant,
                skill_id=skill_id,
                require_all_dynamic_values=True,
            )
            result = await original_list(
                self, subsystem, action, field,
                tenant=tenant, query=query, cursor=cursor, limit=limit, context=decoded_context,
            )
            if result.get("source_status") not in {"ok", "empty"}:
                return result
            return await _issue_option_references(
                result,
                tenant=tenant,
                skill_id=skill_id,
                field=field,
                select=select,
                context=decoded_context,
            )
        except OptionReferenceError as exc:
            return _error_result(field, exc)

    async def invoke_skill_with_references(
        self,
        subsystem,
        action: str,
        fields: dict,
        *,
        tenant: str = "a-corp",
        confirm: bool = False,
    ):
        skill, api_request = await _api_request_for(self, subsystem, action)
        if not reference_required(api_request):
            return await original_invoke(
                self, subsystem, action, fields, tenant=tenant, confirm=confirm,
            )
        skill_id = _skill_id(subsystem, action)
        try:
            decoded, _ = await _decode_present_references(
                api_request,
                fields,
                tenant=tenant,
                skill_id=skill_id,
                require_all_dynamic_values=True,
            )
        except OptionReferenceError as exc:
            return TaskOutcome(
                task_id=uuid4(),
                state=TaskState.NEEDS_INPUT,
                skill_id=getattr(skill, "skill_id", skill_id),
                message=str(exc),
                audit={"option_reference": {"code": getattr(exc, "code", "invalid_option_reference")}},
            )
        return await original_invoke(
            self, subsystem, action, decoded, tenant=tenant, confirm=confirm,
        )

    Orchestrator.list_field_options = list_field_options_with_references
    Orchestrator.invoke_skill = invoke_skill_with_references
    _INSTALLED = True
