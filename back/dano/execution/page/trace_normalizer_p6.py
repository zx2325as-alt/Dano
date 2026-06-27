"""P6 CaptureBundle to Trace IR normalization."""
from __future__ import annotations

from urllib.parse import urlparse

from dano.execution.page.trace_ir import TraceEvent, TraceIR, trace_to_dict


def _path(url: str | None) -> str:
    value = str(url or "")
    if not value:
        return ""
    parsed = urlparse(value)
    return (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")


def _network_payload(payload: dict) -> dict:
    return {
        "method": payload.get("method"),
        "url": payload.get("url"),
        "path": _path(payload.get("url")),
        "role": payload.get("role"),
        "content_type": payload.get("content_type"),
        "status": payload.get("status"),
        "has_body": bool(payload.get("has_body")),
        "has_response": bool(payload.get("has_response")),
        "body_hash": payload.get("body_hash"),
        "response_hash": payload.get("response_hash"),
        "count": payload.get("count"),
        "header_names": list(payload.get("header_names") or []),
        "credential_header_names": list(payload.get("credential_header_names") or []),
    }


def _role(payload: dict) -> str:
    role = str(payload.get("role") or "").lower()
    if role in {"read", "write"}:
        return role
    return "read" if str(payload.get("method") or "GET").upper() in {"GET", "HEAD", "OPTIONS"} else "write"


def _timeline(bundle: dict) -> list[TraceEvent]:
    raw_events = sorted(
        [item for item in bundle.get("timeline") or [] if isinstance(item, dict)],
        key=lambda item: (
            item.get("sequence") if isinstance(item.get("sequence"), int) else 10**12,
            item.get("monotonic_ns") if isinstance(item.get("monotonic_ns"), int) else 10**30,
            str(item.get("event_id") or ""),
        ),
    )
    events: list[TraceEvent] = []
    event_map: dict[str, str] = {}
    request_map: dict[str, str] = {}
    for order, raw in enumerate(raw_events):
        source_id = str(raw.get("event_id") or f"capture-{order:04d}")
        raw_type = str(raw.get("type") or "")
        payload = raw.get("payload") or {}
        request_id = str(raw.get("request_id") or "") or None
        if raw_type == "ui":
            event_type = "ui." + str(payload.get("op") or "action")
            event_id = f"evt-ui-{order:04d}"
            normalized = {
                "op": payload.get("op"),
                "field": payload.get("field"),
                "locator": payload.get("locator"),
                "page_url": payload.get("page_url"),
                "has_value": bool(payload.get("has_value")),
                "value_hash": payload.get("value_hash"),
                "required": bool(payload.get("required")),
            }
        elif raw_type == "network.request":
            kind = _role(payload)
            event_type = f"network.{kind}"
            event_id = f"evt-{kind}-{order:04d}"
            normalized = _network_payload(payload)
        elif raw_type == "network.response":
            event_type = "network.response"
            event_id = f"evt-response-{order:04d}"
            normalized = _network_payload(payload)
        else:
            event_type = raw_type or "capture.event"
            event_id = f"evt-event-{order:04d}"
            normalized = dict(payload)
        parent = str(raw.get("parent_event_id") or "")
        causes: list[str] = []
        if parent in event_map:
            causes.append(event_map[parent])
        elif raw_type == "network.response" and request_id in request_map:
            causes.append(request_map[request_id])
        event = TraceEvent(
            event_id=event_id,
            type=event_type,
            order=order,
            sequence=raw.get("sequence") if isinstance(raw.get("sequence"), int) else order,
            monotonic_ns=raw.get("monotonic_ns") if isinstance(raw.get("monotonic_ns"), int) else None,
            wall_time_ns=raw.get("wall_time_ns") if isinstance(raw.get("wall_time_ns"), int) else None,
            correlation_id=request_id,
            source_event_id=source_id,
            evidence_ref=f"capture://timeline/{source_id}",
            caused_by=causes,
            payload=normalized,
        )
        events.append(event)
        event_map[source_id] = event_id
        if raw_type == "network.request" and request_id:
            request_map[request_id] = event_id
    return events


def _legacy_ui(step: dict, order: int) -> TraceEvent:
    return TraceEvent(
        event_id=f"evt-ui-{order:04d}",
        type="ui." + str(step.get("op") or "action"),
        order=order,
        sequence=order,
        evidence_ref=f"capture://ui_steps/{order}",
        payload={
            "op": step.get("op"),
            "field": step.get("field"),
            "locator": step.get("locator"),
            "has_value": step.get("value") not in (None, ""),
            "required": bool(step.get("required")),
        },
    )


def _legacy_network(kind: str, fact: dict, order: int, causes: list[str]) -> TraceEvent:
    return TraceEvent(
        event_id=f"evt-{kind}-{order:04d}",
        type=f"network.{kind}",
        order=order,
        sequence=order,
        correlation_id=str(fact.get("id") or "") or None,
        evidence_ref=f"capture://{kind}s/{fact.get('id')}",
        caused_by=list(causes),
        payload=_network_payload({**fact, "role": kind}),
    )


def normalize_capture_bundle(bundle: dict) -> dict:
    events = _timeline(bundle or {})
    if not events:
        for step in (bundle or {}).get("ui_steps") or []:
            events.append(_legacy_ui(step, len(events)))
        tail = [events[-1].event_id] if events else []
        for fact in (bundle or {}).get("reads") or []:
            events.append(_legacy_network("read", fact, len(events), tail))
        for fact in (bundle or {}).get("writes") or []:
            events.append(_legacy_network("write", fact, len(events), tail))
    return trace_to_dict(TraceIR(
        capture_id=(bundle or {}).get("capture_id", ""),
        capture_hash=(bundle or {}).get("evidence_hash", ""),
        events=events,
    ))


def event_for_request(trace_ir: dict | None, req: dict | None, kind: str = "write") -> str:
    if not trace_ir or not req:
        return ""
    from dano.execution.page.capture_bundle import content_hash

    url = req.get("url")
    body_hash = content_hash(req.get("post_data")) if req.get("post_data") is not None else ""
    request_id = str(req.get("_capture_id") or req.get("request_id") or "")
    for event in trace_ir.get("events") or []:
        payload = event.get("payload") or {}
        if event.get("type") != f"network.{kind}" or payload.get("url") != url:
            continue
        if request_id and event.get("correlation_id") and event.get("correlation_id") != request_id:
            continue
        if body_hash and payload.get("body_hash") and payload.get("body_hash") != body_hash:
            continue
        return "trace://" + str(event.get("event_id"))
    return ""


def event_for_url(trace_ir: dict | None, url: str | None, kind: str = "write") -> str:
    if not trace_ir or not url:
        return ""
    return event_for_request(trace_ir, {"url": url}, kind)
