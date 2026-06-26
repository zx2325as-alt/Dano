"""Business-facing Skill interface for recorded request skills.

P3 assets project only business inputs, opaque option capabilities and verification
provenance. Unmarked historical assets retain their v1 interface until migrated; that
compatibility path is never used for newly compiled option-reference assets.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from dano.catalog.option_query_manifest_p1 import option_query_schema
from dano.execution.page.request_capture import _leaf_paths
from dano.execution.page.transaction_ir import stable_source_id

SKILL_INTERFACE_VERSION = "skill-interface/v2"
LEGACY_SKILL_INTERFACE_VERSION = "skill-interface/v1"


def _requests(api_request: dict | None) -> list[tuple[int, dict]]:
    apir = api_request or {}
    steps = apir.get("steps")
    if steps:
        return [(i, step or {}) for i, step in enumerate(steps)]
    return [(0, apir)]


def _last_request(api_request: dict | None) -> dict:
    requests = _requests(api_request)
    return requests[-1][1] if requests else {}


def _params(api_request: dict | None) -> list[str]:
    apir = api_request or {}
    params = list(apir.get("params") or [])
    if not params and apir.get("steps"):
        params = list(_last_request(apir).get("params") or [])
    return list(dict.fromkeys(str(item) for item in params if item))


def _field_types(api_request: dict | None) -> dict:
    apir = api_request or {}
    out = dict(apir.get("field_types") or {})
    if not out and apir.get("steps"):
        out = dict(_last_request(apir).get("field_types") or {})
    return out


def _json_type(declared: str | None) -> str:
    if declared in {"array", "object", "number", "integer", "boolean"}:
        return declared
    return "string"


def _selects(api_request: dict | None) -> list[dict]:
    out: list[dict] = []
    for step_index, request in _requests(api_request):
        for select in request.get("selects") or []:
            if isinstance(select, dict):
                out.append({**select, "_step": step_index})
    return out


def _reference_required(api_request: dict | None, select: dict | None = None) -> bool:
    if isinstance(select, dict) and select.get("option_reference_required"):
        return True
    marker = (api_request or {}).get("option_reference") or {}
    return bool(
        isinstance(marker, dict)
        and marker.get("version") == "option-reference/v1"
        and marker.get("required")
    )


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _capability_id(select: dict) -> str:
    source_identity = select.get("source_fingerprint") or stable_source_id(
        select.get("source_url"), select.get("value_key"), select.get("label_key")
    )
    material = {
        "version": SKILL_INTERFACE_VERSION,
        "source": source_identity,
        "kind": select.get("kind") or "single",
    }
    return "optcap_" + hashlib.sha256(_stable_json(material).encode("utf-8")).hexdigest()[:20]


def _safe_query_capabilities(select: dict) -> dict:
    projected = option_query_schema(select)
    return {
        "search": bool(projected.get("x-options-search")),
        "pagination": str(projected.get("x-options-pagination") or ""),
        "depends_on": list(projected.get("x-options-depends-on") or []),
        "validation": bool(projected.get("x-options-validation")),
        "min_query_length": int(projected.get("x-options-min-query-length") or 0),
    }


def _option_capabilities(api_request: dict | None, selects: list[dict]) -> dict[str, dict]:
    capabilities: dict[str, dict] = {}
    for select in selects:
        capability_id = _capability_id(select)
        capability = capabilities.setdefault(
            capability_id,
            {
                "id": capability_id,
                "fields": [],
                "kind": "multi" if select.get("kind") == "array" else "single",
                **_safe_query_capabilities(select),
                "reference_required": _reference_required(api_request, select),
            },
        )
        field = str(select.get("param") or "").strip()
        if field and field not in capability["fields"]:
            capability["fields"].append(field)
    return capabilities


def _direct_inputs(api_request: dict | None, select_params: set[str]) -> list[str]:
    inputs: list[str] = []
    for _step_index, request in _requests(api_request):
        template = request.get("body_template")
        if not isinstance(template, (dict, list)):
            continue
        for _path, _tokens, value, _raw in _leaf_paths(template):
            if not (isinstance(value, str) and value.startswith("{{") and value.endswith("}}")):
                continue
            name = value[2:-2]
            if name not in select_params and name not in inputs:
                inputs.append(name)
    return inputs


def _public_bindings(api_request: dict | None, selects: list[dict]) -> list[dict]:
    bindings: list[dict] = []
    select_params = {str(item.get("param")) for item in selects if item.get("param")}
    for name in _direct_inputs(api_request, select_params):
        bindings.append({"input": name, "mode": "direct"})
    for select in selects:
        name = str(select.get("param") or "").strip()
        if not name:
            continue
        bindings.append({
            "input": name,
            "mode": "option_reference" if _reference_required(api_request, select) else "option_value",
            "capability_id": _capability_id(select),
            "multiple": select.get("kind") == "array",
        })
    return bindings


def _input_schema(
    api_request: dict | None,
    params: list[str],
    field_types: dict,
    selects: list[dict],
    required_fields: list[str] | None,
) -> dict:
    select_by_param = {str(item.get("param")): item for item in selects if item.get("param")}
    required = list(dict.fromkeys(required_fields if required_fields is not None else params))
    properties: dict[str, dict] = {}
    for name in params:
        declared = field_types.get(name)
        select = select_by_param.get(name)
        if not select:
            properties[name] = {"type": _json_type(declared)}
            continue
        multiple = select.get("kind") == "array" or declared == "array"
        reference = _reference_required(api_request, select)
        capability_id = _capability_id(select)
        if multiple:
            item_format = "option-reference" if reference else "option-value"
            prop: dict[str, Any] = {
                "type": "array",
                "items": {"type": "string", "format": item_format},
                "format": item_format + "-list",
            }
        else:
            prop = {
                "type": "string",
                "format": "option-reference" if reference else "option-value",
            }
        prop.update({
            "x-option-capability-id": capability_id,
            "x-submit-mode": "reference[]" if reference and multiple else (
                "reference" if reference else ("value[]" if multiple else "value")
            ),
            "x-option-reference-required": reference,
            "x-options-source": True,
            **option_query_schema(select),
        })
        properties[name] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": [name for name in required if name in properties],
        "additionalProperties": False,
    }


def _managed_summary(items: list | None) -> dict:
    count = len([item for item in items or [] if isinstance(item, dict)])
    return {"managed_by_dano": bool(count), "count": count}


def _derived_count(api_request: dict | None, selects: list[dict]) -> int:
    apir = api_request or {}
    last = _last_request(apir)
    count = len([item for item in (apir.get("derived_fields") or last.get("derived_fields") or [])
                 if isinstance(item, dict)])
    for select in selects:
        count += len([item for item in select.get("derived_count_paths") or [] if isinstance(item, dict)])
    return count


# ── v1 compatibility for stored, unmarked assets ─────────────────────────────
def _legacy_source_id(select: dict) -> str:
    return str(select.get("source_id") or stable_source_id(
        select.get("source_url"), select.get("value_key"), select.get("label_key")
    ))


def _legacy_placeholder_bindings(api_request: dict | None, select_params: set[str]) -> list[dict]:
    bindings: list[dict] = []
    for step_index, request in _requests(api_request):
        template = request.get("body_template")
        if not isinstance(template, (dict, list)):
            continue
        for path, tokens, value, _raw in _leaf_paths(template):
            if not (isinstance(value, str) and value.startswith("{{") and value.endswith("}}")):
                continue
            name = value[2:-2]
            if name in select_params:
                continue
            bindings.append({
                "input": name,
                "target_path": path,
                "target_tokens": tokens,
                "mode": "direct",
                "step": step_index,
            })
    return bindings


def _legacy_select_binding(select: dict) -> dict:
    mode = "expand_array" if select.get("kind") == "array" else "select_value"
    return {
        "input": select.get("param"),
        "target_path": select.get("array_path") or select.get("path"),
        "target_tokens": select.get("array_tokens") or select.get("tokens"),
        "mode": mode,
        "source_id": _legacy_source_id(select),
        "target_key": select.get("target_key") or select.get("value_key"),
        "paired_id_path": select.get("id_path"),
        "expand_fields": list((select.get("item_template") or {}).keys())
        if isinstance(select.get("item_template"), dict)
        else list(select.get("expand_fields") or []),
        "step": select.get("_step", 0),
    }


def _legacy_source_schema(selects: list[dict]) -> dict:
    sources: dict[str, dict] = {}
    for select in selects:
        source_id = _legacy_source_id(select)
        source = sources.setdefault(source_id, {
            "id": source_id,
            "kind": "http_list",
            "url": select.get("source_url") or "",
            "fields": [],
            "submit_modes": [],
            "value_key": select.get("value_key") or "",
            "label_key": select.get("label_key") or "",
            "count": select.get("count"),
            "has_runtime_source": bool(select.get("source_url")),
        })
        field = select.get("param")
        if field and field not in source["fields"]:
            source["fields"].append(field)
        mode = select.get("submit_mode") or ("value[]" if select.get("kind") == "array" else "value")
        if mode not in source["submit_modes"]:
            source["submit_modes"].append(mode)
        if select.get("option_filter"):
            source["option_filter"] = dict(select.get("option_filter") or {})
        if select.get("evidence"):
            source["evidence"] = list(select.get("evidence") or [])
    return sources


def _legacy_derived(api_request: dict | None, selects: list[dict]) -> list[dict]:
    apir = api_request or {}
    last = _last_request(apir)
    out = copy.deepcopy(apir.get("derived_fields") or last.get("derived_fields") or [])
    seen = {
        (item.get("kind") or "mirror", item.get("target_path"), item.get("source_path"), item.get("input"))
        for item in out if isinstance(item, dict)
    }
    for select in selects:
        if select.get("kind") != "array":
            continue
        for derived in select.get("derived_count_paths") or []:
            item = {
                "kind": "array_count",
                "input": select.get("param"),
                "source_path": select.get("array_path") or select.get("path"),
                "target_path": derived.get("path"),
                "target_tokens": derived.get("tokens"),
                "step": select.get("_step", 0),
            }
            key = (item["kind"], item["target_path"], item["source_path"], item["input"])
            if item["target_path"] and key not in seen:
                seen.add(key)
                out.append(item)
    return out


def _legacy_input_schema(params: list[str], field_types: dict, selects: list[dict],
                         required_fields: list[str] | None) -> dict:
    select_by_param = {select.get("param"): select for select in selects if select.get("param")}
    required = list(dict.fromkeys(required_fields if required_fields is not None else params))
    properties: dict[str, dict] = {}
    for name in params:
        declared = field_types.get(name)
        prop: dict[str, Any] = {"type": _json_type(declared)}
        if declared == "enum":
            prop.update({"type": "string", "format": "name-ref"})
        if declared == "array":
            prop.update({"type": "array", "items": {"type": "string"}, "format": "name-ref-list"})
        select = select_by_param.get(name)
        if select:
            prop["x-source-id"] = _legacy_source_id(select)
            prop["x-submit-mode"] = select.get("submit_mode") or (
                "value[]" if select.get("kind") == "array" else "value"
            )
            prop["x-option-label"] = "label"
            prop["x-option-value"] = "value"
            prop["x-options-source"] = bool(select.get("source_url"))
        properties[name] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _build_legacy_interface(api_request: dict, required_fields: list[str] | None) -> dict:
    params = _params(api_request)
    field_types = _field_types(api_request)
    selects = _selects(api_request)
    select_params = {select.get("param") for select in selects if select.get("param")}
    bindings = _legacy_placeholder_bindings(api_request, select_params)
    bindings.extend(_legacy_select_binding(select) for select in selects if select.get("param"))
    last = _last_request(api_request)
    transaction_ir = api_request.get("transaction_ir") if isinstance(api_request.get("transaction_ir"), dict) else {}
    capture = copy.deepcopy((transaction_ir or {}).get("capture") or {})
    return {
        "version": LEGACY_SKILL_INTERFACE_VERSION,
        "input_schema": _legacy_input_schema(params, field_types, selects, required_fields),
        "source_schema": _legacy_source_schema(selects),
        "bindings": bindings,
        "identity": copy.deepcopy(api_request.get("identity") or last.get("identity") or []),
        "derived": _legacy_derived(api_request, selects),
        "success": copy.deepcopy(api_request.get("success_rule") or last.get("success_rule") or {}),
        "provenance": {
            "transaction_ir_version": (transaction_ir or {}).get("version"),
            "capture_hash": capture.get("capture_hash"),
            "trace_hash": capture.get("trace_hash"),
            "write_event": capture.get("write_event"),
        },
    }


def build_skill_interface(
    api_request: dict | None,
    *,
    required_fields: list[str] | None = None,
) -> dict:
    """Project v2 for P3 assets; preserve v1 only for stored unmarked assets."""
    apir = api_request or {}
    if not _reference_required(apir):
        return _build_legacy_interface(apir, required_fields)

    params = _params(apir)
    field_types = _field_types(apir)
    selects = _selects(apir)
    capabilities = _option_capabilities(apir, selects)
    last = _last_request(apir)
    transaction_ir = apir.get("transaction_ir") if isinstance(apir.get("transaction_ir"), dict) else {}
    capture = copy.deepcopy((transaction_ir or {}).get("capture") or {})
    identity_items = apir.get("identity") or last.get("identity") or []
    derived_count = _derived_count(apir, selects)
    success_rule = apir.get("success_rule") or last.get("success_rule") or {}
    fact_check = apir.get("fact_check") or last.get("fact_check") or {}

    return {
        "version": SKILL_INTERFACE_VERSION,
        "input_schema": _input_schema(apir, params, field_types, selects, required_fields),
        "source_schema": capabilities,
        "option_capabilities": capabilities,
        "bindings": _public_bindings(apir, selects),
        "identity": _managed_summary(identity_items),
        "derived": {"managed_by_dano": bool(derived_count), "count": derived_count},
        "success": {
            "response_rule": bool(success_rule),
            "fact_check": bool(fact_check),
        },
        "provenance": {
            "transaction_ir_version": (transaction_ir or {}).get("version"),
            "capture_hash": capture.get("capture_hash"),
            "trace_hash": capture.get("trace_hash"),
            "write_event": capture.get("write_event"),
        },
    }
