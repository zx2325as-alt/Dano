"""Business-facing Skill interface for recorded request skills.

The executable ``api_request`` remains backend-private. This module projects only what a
caller needs: business inputs, option-query capabilities and verification provenance.
Target URLs, request paths, body token paths, label/value keys, identity sources,
recorded option snapshots and success-rule internals are deliberately excluded.
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


def build_skill_interface(
    api_request: dict | None,
    *,
    required_fields: list[str] | None = None,
) -> dict:
    """Project a stable public contract without exposing executable request internals."""
    apir = api_request or {}
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
        # Keep the historical key for manifest compatibility, but values are now opaque
        # business capabilities rather than executable source schemas.
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
