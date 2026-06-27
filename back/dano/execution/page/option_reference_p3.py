"""P3 opaque option-reference asset contract.

Newly compiled request-captured skills no longer treat target-system IDs as public input.
A dynamic select is marked as requiring a short-lived broker reference. Runtime issuance
and redemption are installed at the orchestrator boundary; the existing P0/P1 live
candidate validation still proves the decoded value before a write is sent.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable

_INSTALLED = False
REFERENCE_VERSION = "option-reference/v1"
_SOURCE_KEYS = (
    "source_url",
    "source_method",
    "source_post_data",
    "source_content_type",
    "source_query",
    "source_records_path",
    "value_key",
    "label_key",
    "option_filter",
    "option_query",
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def source_fingerprint(select: dict | None) -> str:
    """Hash the executable source contract without credential material."""
    select = select or {}
    material = {key: copy.deepcopy(select.get(key)) for key in _SOURCE_KEYS if key in select}
    return "optsrc_" + hashlib.sha256(_stable_json(material).encode("utf-8")).hexdigest()[:24]


def _selects_of(api_request: dict | None) -> list[dict]:
    if not isinstance(api_request, dict):
        return []
    out = [item for item in api_request.get("selects") or [] if isinstance(item, dict)]
    for step in api_request.get("steps") or []:
        if isinstance(step, dict):
            out.extend(item for item in step.get("selects") or [] if isinstance(item, dict))
    return out


def dynamic_selects(api_request: dict | None) -> list[dict]:
    return [item for item in _selects_of(api_request) if item.get("param") and item.get("source_url")]


def reference_required(api_request: dict | None) -> bool:
    marker = (api_request or {}).get("option_reference") or {}
    return bool(isinstance(marker, dict) and marker.get("version") == REFERENCE_VERSION and marker.get("required"))


def _mark_compiled(api_request: dict | None) -> dict | None:
    if not isinstance(api_request, dict):
        return api_request
    selects = dynamic_selects(api_request)
    if not selects:
        return api_request
    api_request["option_reference"] = {
        "version": REFERENCE_VERSION,
        "required": True,
        "legacy_raw_values": False,
    }
    for select in selects:
        select["option_reference_required"] = True
        select["source_fingerprint"] = source_fingerprint(select)
    return api_request


def _wrap_builder(original: Callable):
    def wrapped(*args, **kwargs):
        return _mark_compiled(original(*args, **kwargs))

    return wrapped


def install_option_reference_p3() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from dano.execution.page import request_capture as rc

    rc.build_api_request = _wrap_builder(rc.build_api_request)
    rc.build_api_workflow = _wrap_builder(rc.build_api_workflow)
    _INSTALLED = True
