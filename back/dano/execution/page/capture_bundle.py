"""CaptureBundle: immutable raw facts captured during page recording.

CaptureBundle contains enough redacted request/response material to rebuild Trace IR and
rerun deterministic dataflow analysis without recording the page again. Credential values
are never persisted here; only header names and storage topology are retained.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any
from uuid import uuid4

CAPTURE_BUNDLE_VERSION = "capture-bundle/v2"
_SECRET_HEADER_HINTS = (
    "authorization", "cookie", "token", "secret", "api-key", "apikey",
    "session", "credential", "signature", "satoken",
)


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(obj: Any) -> str:
    return hashlib.sha256(_stable_json(obj).encode("utf-8")).hexdigest()


def _storage_summary(storage_state: dict | None) -> dict:
    if not storage_state:
        return {}
    origins = []
    for origin in storage_state.get("origins") or []:
        origins.append({
            "origin": origin.get("origin"),
            "local_storage_keys": sorted([
                item.get("name") for item in (origin.get("localStorage") or []) if item.get("name")
            ]),
        })
    cookies = sorted([cookie.get("name") for cookie in (storage_state.get("cookies") or []) if cookie.get("name")])
    return {"cookie_names": cookies, "origins": origins}


def _header_metadata(headers: dict | None) -> tuple[list[str], list[str]]:
    names = sorted({str(name) for name, value in (headers or {}).items() if name and value is not None})
    credential_names = sorted([
        name for name in names if any(hint in name.lower() for hint in _SECRET_HEADER_HINTS)
    ])
    return names, credential_names


def _request_fact(request: dict, index: int, kind: str) -> dict:
    body = request.get("post_data")
    response = request.get("response_json") if "response_json" in request else request.get("json")
    header_names, credential_header_names = _header_metadata(request.get("headers"))
    request_id = str(request.get("_capture_id") or request.get("request_id") or f"{kind}-{index}")
    raw = {
        "request_id": request_id,
        "method": str(request.get("method") or ("GET" if kind == "read" else "POST")).upper(),
        "url": request.get("url") or "",
        "content_type": request.get("content_type") or "",
        "status": request.get("status"),
        "post_data": copy.deepcopy(body),
        "response_json": copy.deepcopy(response),
        "count": request.get("count"),
        "header_names": header_names,
        "credential_header_names": credential_header_names,
        "request_event_id": request.get("_request_event_id"),
        "response_event_id": request.get("_response_event_id"),
        "request_sequence": request.get("_request_sequence"),
        "response_sequence": request.get("_response_sequence"),
    }
    fact = {
        "id": request_id,
        "method": raw["method"],
        "url": raw["url"],
        "content_type": raw["content_type"],
        "status": raw["status"],
        "body_hash": content_hash(body) if body is not None else "",
        "response_hash": content_hash(response) if response is not None else "",
        "has_body": body is not None,
        "has_response": response is not None,
        "count": raw["count"],
        "request_event_id": raw["request_event_id"],
        "response_event_id": raw["response_event_id"],
        "request_sequence": raw["request_sequence"],
        "response_sequence": raw["response_sequence"],
        "header_names": header_names,
        "credential_header_names": credential_header_names,
        "raw": raw,
    }
    return fact


def _sanitize_timeline_event(event: dict) -> dict:
    payload = copy.deepcopy(event.get("payload") or {})
    headers = payload.pop("headers", None)
    if headers:
        names, credential_names = _header_metadata(headers)
        payload["header_names"] = names
        payload["credential_header_names"] = credential_names
    post_data = payload.pop("post_data", None)
    if post_data is not None:
        payload["has_body"] = True
        payload["body_hash"] = content_hash(post_data)
    response = payload.pop("response_json", None)
    if response is not None:
        payload["has_response"] = True
        payload["response_hash"] = content_hash(response)
    value = payload.pop("value", None)
    if value not in (None, ""):
        payload["has_value"] = True
        payload["value_hash"] = content_hash(value)
    return {
        "event_id": str(event.get("event_id") or ""),
        "type": str(event.get("type") or ""),
        "sequence": event.get("sequence"),
        "monotonic_ns": event.get("monotonic_ns"),
        "wall_time_ns": event.get("wall_time_ns"),
        "request_id": event.get("request_id"),
        "parent_event_id": event.get("parent_event_id"),
        "payload": payload,
    }


@dataclass
class CaptureBundle:
    version: str = CAPTURE_BUNDLE_VERSION
    capture_id: str = field(default_factory=lambda: "capture-" + uuid4().hex[:10])
    start_url: str = ""
    ui_steps: list[dict] = field(default_factory=list)
    samples: dict = field(default_factory=dict)
    required_labels: list[str] = field(default_factory=list)
    writes: list[dict] = field(default_factory=list)
    reads: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    storage: dict = field(default_factory=dict)
    evidence_hash: str = ""


def build_capture_bundle(*, start_url: str = "", steps: list[dict] | None = None,
                          writes: list[dict] | None = None, reads: list[dict] | None = None,
                          timeline: list[dict] | None = None,
                          storage_state: dict | None = None, samples: dict | None = None,
                          required_labels: set | list | None = None,
                          capture_id: str | None = None) -> dict:
    writes_value = writes or []
    reads_value = reads or []
    inherited_timeline = timeline
    if inherited_timeline is None:
        inherited_timeline = getattr(writes_value, "timeline", None)
    if inherited_timeline is None:
        inherited_timeline = getattr(reads_value, "timeline", None)
    bundle = CaptureBundle(
        capture_id=capture_id or "capture-" + uuid4().hex[:10],
        start_url=start_url or "",
        ui_steps=copy.deepcopy(list(steps or [])),
        samples=copy.deepcopy(dict(samples or {})),
        required_labels=sorted(list(required_labels or [])),
        writes=[_request_fact(request, index, "write") for index, request in enumerate(writes_value)],
        reads=[_request_fact(request, index, "read") for index, request in enumerate(reads_value)],
        timeline=[_sanitize_timeline_event(event) for event in (inherited_timeline or [])],
        storage=_storage_summary(storage_state),
    )
    data = asdict(bundle)
    data["evidence_hash"] = content_hash({key: value for key, value in data.items() if key != "evidence_hash"})
    return data


def capture_integrity_issues(bundle: dict | None) -> list[str]:
    if not isinstance(bundle, dict):
        return ["capture bundle must be an object"]
    issues: list[str] = []
    expected = content_hash({key: value for key, value in bundle.items() if key != "evidence_hash"})
    if bundle.get("evidence_hash") != expected:
        issues.append("capture evidence hash mismatch")
    for kind in ("writes", "reads"):
        for index, fact in enumerate(bundle.get(kind) or []):
            raw = (fact or {}).get("raw") or {}
            body = raw.get("post_data")
            response = raw.get("response_json")
            body_hash = content_hash(body) if body is not None else ""
            response_hash = content_hash(response) if response is not None else ""
            if fact.get("body_hash") != body_hash:
                issues.append(f"{kind}[{index}] body hash mismatch")
            if fact.get("response_hash") != response_hash:
                issues.append(f"{kind}[{index}] response hash mismatch")
            if any(value for value in (raw.get("headers") or {}).values()):
                issues.append(f"{kind}[{index}] contains credential header values")
    return issues


def raw_writes(bundle: dict) -> list[dict]:
    return [copy.deepcopy(write.get("raw")) for write in (bundle or {}).get("writes") or []
            if isinstance(write.get("raw"), dict)]


def raw_reads(bundle: dict) -> list[dict]:
    return [copy.deepcopy(read.get("raw")) for read in (bundle or {}).get("reads") or []
            if isinstance(read.get("raw"), dict)]
