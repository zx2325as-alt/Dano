"""P2 deterministic inference for typed option-query capabilities.

P1 defines how a recorded option source is queried. P2 learns the parts that can be
proved from browser evidence and refuses to guess the rest.

Inference is deliberately evidence-bound:

* search bindings require a recent UI ``fill`` value to occur at one unique request path;
* cursor pagination requires the next request cursor to occur in the prior response;
* page/offset pagination requires a monotonic numeric sequence plus a semantic key;
* exact validation requires the selected stable value at an id-like request path and in
  the returned option record;
* dependencies require a request value to be tied to another recorded business field.

Every active relation carries evidence references and confidence. Low-confidence
candidates are not compiled into ``option_query``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_INSTALLED = False
_MIN_AUTO_CONFIDENCE = 0.90
_UI_WINDOW_MS = 3_000
_MAX_UI_EVENTS = 200
_QUERY_META_KEYS = (
    "option_query",
    "option_query_inference",
)


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _next_capture_ref(session, kind: str) -> str:
    seq = int(getattr(session, "_option_capture_seq", 0)) + 1
    session._option_capture_seq = seq
    return f"{kind}:{seq}"


def _fingerprint(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raw = repr(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalized_endpoint(url: str | None) -> str:
    parsed = urlsplit(str(url or ""))
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", ""))


def _safe_query(url: str | None) -> dict[str, str] | None:
    pairs = parse_qsl(urlsplit(str(url or "")).query, keep_blank_values=True)
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        return None
    return dict(pairs)


def _walk_scalars(node: Any, path: list[str | int] | None = None):
    path = list(path or [])
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_scalars(value, path + [str(key)])
        return
    if isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_scalars(value, path + [index])
        return
    yield path, node


def _decode_request_body(read: dict) -> tuple[str | None, Any]:
    post_data = read.get("post_data")
    if post_data in (None, ""):
        return None, None
    content_type = str(read.get("content_type") or "").lower()
    if isinstance(post_data, (dict, list)):
        return "json", copy.deepcopy(post_data)
    text = str(post_data)
    if "json" in content_type or text.lstrip().startswith(("{", "[")):
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return None, None
        if isinstance(value, (dict, list)):
            return "json", value
        return None, None
    if "form-urlencoded" in content_type or "=" in text:
        pairs = parse_qsl(text, keep_blank_values=True)
        keys = [key for key, _ in pairs]
        if pairs and len(keys) == len(set(keys)):
            return "form", dict(pairs)
    return None, None


def _request_slots(read: dict) -> list[dict]:
    slots: list[dict] = []
    query = _safe_query(read.get("url"))
    if query is not None:
        for key, value in query.items():
            slots.append({"location": "query", "path": [key], "value": value})
    location, body = _decode_request_body(read)
    if location == "json":
        for path, value in _walk_scalars(body):
            if path:
                slots.append({"location": "json", "path": path, "value": value})
    elif location == "form" and isinstance(body, dict):
        for key, value in body.items():
            slots.append({"location": "form", "path": [str(key)], "value": value})
    return slots


def _slot_signature(slot: dict) -> tuple[str, tuple[str | int, ...]]:
    return str(slot.get("location") or ""), tuple(slot.get("path") or [])


def _scalar_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if type(left) is type(right):
        return left == right
    return str(left) == str(right)


def _last_key(path: list[str | int] | None) -> str:
    for token in reversed(path or []):
        if not isinstance(token, int):
            return str(token)
    return ""


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _page_role(path: list[str | int]) -> str | None:
    key = _norm_key(_last_key(path))
    if key in {"page", "pageno", "pagenum", "pageindex", "current", "currentpage"}:
        return "page"
    if key in {"offset", "start", "startindex", "skip", "from"}:
        return "offset"
    if "cursor" in key or key in {"after", "continuation", "continuationtoken"}:
        return "cursor"
    return None


def _size_role(path: list[str | int]) -> bool:
    key = _norm_key(_last_key(path))
    return key in {
        "size", "pagesize", "limit", "perpage", "rows", "take", "maxresults",
    }


def _id_role(path: list[str | int]) -> bool:
    key = _norm_key(_last_key(path))
    return bool(key) and (
        key in {"id", "value", "key", "code", "uid", "userid"}
        or key.endswith(("id", "ids", "code", "key", "uuid", "guid"))
    )


def _response_scalar_paths(node: Any, value: Any) -> list[list[str | int]]:
    return [path for path, current in _walk_scalars(node) if path and _scalar_equal(current, value)]


def _response_semantic_path(node: Any, role: str, item_count: int) -> list[str | int] | None:
    candidates: list[list[str | int]] = []
    for path, value in _walk_scalars(node):
        key = _norm_key(_last_key(path))
        if role == "has_more":
            if isinstance(value, bool) and key in {"hasmore", "hasnext", "more", "moreresults"}:
                candidates.append(path)
        elif role == "total":
            if isinstance(value, int) and not isinstance(value, bool) and value >= item_count and (
                key == "total" or key.endswith("total") or key in {"totalcount", "recordcount"}
            ):
                candidates.append(path)
    return candidates[0] if len(candidates) == 1 else None


def _items(read: dict) -> list:
    from dano.execution.page import request_capture as rc

    return list(rc.as_list_payload(read.get("json")) or [])


def _read_ref(read: dict, index: int) -> str:
    return str(read.get("_capture_ref") or f"read:{index}")


def _ui_events(read: dict) -> list[dict]:
    value = read.get("_ui_evidence") or []
    return [item for item in value if isinstance(item, dict)]


def _source_reads(select: dict, reads: list[dict]) -> list[tuple[int, dict]]:
    endpoint = _normalized_endpoint(select.get("source_url"))
    method = str(select.get("source_method") or "").upper()
    matches: list[tuple[int, dict]] = []
    for index, read in enumerate(reads or []):
        if _normalized_endpoint(read.get("url")) != endpoint:
            continue
        read_method = str(read.get("method") or "GET").upper()
        if method and read_method != method:
            continue
        matches.append((index, read))
    if matches:
        return matches
    # Legacy selections can lack source_method; endpoint match is still deterministic.
    return [
        (index, read)
        for index, read in enumerate(reads or [])
        if _normalized_endpoint(read.get("url")) == endpoint
    ]


def _normalize_source(select: dict, source_reads: list[tuple[int, dict]]) -> None:
    if not source_reads:
        return
    _index, read = source_reads[-1]
    parsed = urlsplit(str(read.get("url") or select.get("source_url") or ""))
    query = _safe_query(read.get("url"))
    if query is not None:
        select["source_url"] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
        if query:
            select["source_query"] = query
    select["source_method"] = str(read.get("method") or select.get("source_method") or "GET").upper()


def _search_inference(source_reads: list[tuple[int, dict]]) -> tuple[dict | None, dict | None]:
    observations: dict[tuple[str, tuple[str | int, ...]], list[tuple[str, str]]] = {}
    empty_signatures: set[tuple[str, tuple[str | int, ...]]] = set()
    for index, read in source_reads:
        slots = _request_slots(read)
        for slot in slots:
            if slot.get("value") in (None, ""):
                empty_signatures.add(_slot_signature(slot))
        for event in _ui_events(read):
            if event.get("op") != "fill" or event.get("value") in (None, ""):
                continue
            matching = [slot for slot in slots if _scalar_equal(slot.get("value"), event.get("value"))]
            if len(matching) != 1:
                continue
            signature = _slot_signature(matching[0])
            observations.setdefault(signature, []).append(
                (_read_ref(read, index), str(event.get("ref") or ""))
            )
    if not observations:
        return None, None
    ranked = sorted(observations.items(), key=lambda item: len(item[1]), reverse=True)
    if len(ranked) > 1 and len(ranked[0][1]) == len(ranked[1][1]):
        return None, None
    signature, refs = ranked[0]
    confidence = 0.98 if len(refs) >= 2 else 0.94
    if confidence < _MIN_AUTO_CONFIDENCE:
        return None, None
    location, path = signature
    evidence = sorted({ref for pair in refs for ref in pair if ref})
    spec = {
        "location": location,
        "path": list(path),
        "min_length": 0,
        "required": signature not in empty_signatures and len(refs) >= 2,
    }
    meta = {
        "kind": "search",
        "confidence": confidence,
        "evidence_refs": evidence,
        "reason": "recent UI fill value uniquely matched the option-source request",
    }
    return spec, meta


def _pagination_inference(source_reads: list[tuple[int, dict]], search: dict | None) -> tuple[dict | None, dict, list[dict]]:
    if len(source_reads) < 2:
        return None, {}, []
    search_sig = _slot_signature(search) if search else None
    rows: dict[tuple[str, tuple[str | int, ...]], list[tuple[int, Any, dict]]] = {}
    for order, (index, read) in enumerate(source_reads):
        for slot in _request_slots(read):
            signature = _slot_signature(slot)
            if signature == search_sig:
                continue
            rows.setdefault(signature, []).append((order, slot.get("value"), read))

    candidates: list[tuple[float, dict, dict, list[dict]]] = []
    for signature, values in rows.items():
        if len(values) != len(source_reads):
            continue
        distinct = []
        for _order, value, _read in values:
            if not any(_scalar_equal(value, seen) for seen in distinct):
                distinct.append(value)
        if len(distinct) < 2:
            continue
        location, path = signature
        semantic = _page_role(list(path))
        ordered = sorted(values, key=lambda item: item[0])
        response: dict = {}
        evidence: list[dict] = []

        # Strongest proof: the next request cursor is present in the previous response.
        response_paths: list[list[str | int]] = []
        linked = True
        for position in range(len(ordered) - 1):
            _order, _current, read = ordered[position]
            next_value = ordered[position + 1][1]
            paths = _response_scalar_paths(read.get("json"), next_value)
            if len(paths) != 1:
                linked = False
                break
            response_paths.append(paths[0])
        if linked and response_paths and all(path_item == response_paths[0] for path_item in response_paths):
            mode = semantic or ("page" if all(isinstance(item[1], int) for item in ordered) else "cursor")
            response["next_cursor_path"] = response_paths[0]
            refs = [_read_ref(read, source_reads[pos][0]) for pos, (_o, _v, read) in enumerate(ordered[:-1])]
            evidence.append({
                "kind": "pagination",
                "confidence": 0.99,
                "evidence_refs": refs,
                "reason": "the next request cursor was found at one stable response path",
            })
            candidates.append((0.99, {
                "mode": mode,
                "location": location,
                "path": list(path),
            }, response, evidence))
            continue

        numeric = [item[1] for item in ordered]
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in numeric):
            continue
        if semantic == "page" and all(numeric[i + 1] == numeric[i] + 1 for i in range(len(numeric) - 1)):
            confidence = 0.95
            reason = "page request value increased by one across repeated source reads"
            mode = "page"
        elif semantic == "offset" and all(
            numeric[i + 1] == numeric[i] + len(_items(ordered[i][2]))
            for i in range(len(numeric) - 1)
        ):
            confidence = 0.96
            reason = "offset increased by the previous page item count"
            mode = "offset"
        else:
            continue
        refs = [_read_ref(read, index) for index, read in source_reads]
        evidence.append({
            "kind": "pagination",
            "confidence": confidence,
            "evidence_refs": refs,
            "reason": reason,
        })
        candidates.append((confidence, {
            "mode": mode,
            "location": location,
            "path": list(path),
        }, response, evidence))

    if not candidates:
        return None, {}, []
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None, {}, []
    _confidence, pagination, response, evidence = candidates[0]

    # A page-size field is safe only when its semantic key is explicit and its recorded
    # value is stable across the source reads.
    size_candidates: list[tuple[str, tuple[str | int, ...], int]] = []
    for signature, values in rows.items():
        if signature == _slot_signature(pagination):
            continue
        location, path = signature
        raw_values = [value for _order, value, _read in values]
        if len(raw_values) != len(source_reads) or not raw_values:
            continue
        if not all(_scalar_equal(raw_values[0], value) for value in raw_values[1:]):
            continue
        if not isinstance(raw_values[0], int) or isinstance(raw_values[0], bool):
            continue
        if not (1 <= raw_values[0] <= 100) or not _size_role(list(path)):
            continue
        size_candidates.append((location, path, raw_values[0]))
    if len(size_candidates) == 1:
        size_location, size_path, default_size = size_candidates[0]
        pagination["size_location"] = size_location
        pagination["size_path"] = list(size_path)
        pagination["default_size"] = default_size
        pagination["max_size"] = 100

    first_items = len(_items(source_reads[0][1]))
    has_more_path = _response_semantic_path(source_reads[0][1].get("json"), "has_more", first_items)
    total_path = _response_semantic_path(source_reads[0][1].get("json"), "total", first_items)
    if has_more_path:
        response["has_more_path"] = has_more_path
    if total_path:
        response["total_path"] = total_path
    return pagination, response, evidence


def _validation_inference(select: dict, source_reads: list[tuple[int, dict]], excluded: set[tuple[str, tuple[str | int, ...]]]) -> tuple[dict | None, dict | None]:
    selected = select.get("value")
    value_key = select.get("value_key")
    if selected in (None, "") or not value_key:
        return None, None
    candidates: dict[tuple[str, tuple[str | int, ...]], list[str]] = {}
    for index, read in source_reads:
        items = _items(read)
        if not any(isinstance(item, dict) and _scalar_equal(item.get(value_key), selected) for item in items):
            continue
        for slot in _request_slots(read):
            signature = _slot_signature(slot)
            if signature in excluded or not _id_role(slot.get("path") or []):
                continue
            if _scalar_equal(slot.get("value"), selected):
                candidates.setdefault(signature, []).append(_read_ref(read, index))
    if len(candidates) != 1:
        return None, None
    signature, refs = next(iter(candidates.items()))
    location, path = signature
    return (
        {"location": location, "path": list(path)},
        {
            "kind": "validation",
            "confidence": 0.98,
            "evidence_refs": sorted(set(refs)),
            "reason": "selected stable value matched one id-like request path and returned record",
        },
    )


def _field_candidates(selects: list[dict], samples: dict | None) -> list[dict]:
    from dano.execution.page import request_capture as rc

    names = rc.suggest_select_names(selects, samples or {})
    out: list[dict] = []
    for select in selects:
        path = str(select.get("path") or "")
        name = names.get(path) or select.get("param")
        if name and select.get("value") not in (None, ""):
            out.append({"field": str(name), "path": path, "value": select.get("value")})
    for field, value in (samples or {}).items():
        if value not in (None, ""):
            out.append({"field": str(field), "path": "", "value": value})
    return out


def _dependency_inference(select: dict, anchor: dict, field_candidates: list[dict], excluded: set[tuple[str, tuple[str | int, ...]]]) -> tuple[list[dict], list[dict]]:
    current_path = str(select.get("path") or "")
    dependencies: list[dict] = []
    evidence: list[dict] = []
    used_fields: set[str] = set()
    events = [event for event in _ui_events(anchor) if event.get("op") in {"pick", "select", "fill"}]
    for slot in _request_slots(anchor):
        signature = _slot_signature(slot)
        if signature in excluded:
            continue
        matches = [candidate for candidate in field_candidates
                   if candidate.get("path") != current_path and _scalar_equal(candidate.get("value"), slot.get("value"))]
        event_matches = [event for event in events if _scalar_equal(event.get("value"), slot.get("value"))]
        if len(matches) != 1:
            continue
        match = matches[0]
        field = str(match.get("field") or "")
        if not field or field in used_fields:
            continue
        # A same-value request constant is not enough. Require either a select-derived
        # stable value or direct recent UI evidence for that business field.
        if not match.get("path") and not event_matches:
            continue
        used_fields.add(field)
        dependencies.append({
            "field": field,
            "field_path": match.get("path") or None,
            "location": slot.get("location"),
            "path": list(slot.get("path") or []),
            "required": True,
        })
        refs = [str(anchor.get("_capture_ref") or "")]
        refs.extend(str(event.get("ref") or "") for event in event_matches)
        evidence.append({
            "kind": "dependency",
            "field": field,
            "confidence": 0.94 if event_matches else 0.91,
            "evidence_refs": sorted({ref for ref in refs if ref}),
            "reason": "option-source request value matched another recorded business field",
        })
    return dependencies, evidence


def infer_option_query(select: dict, reads: list[dict], selects: list[dict], samples: dict | None = None) -> tuple[dict | None, dict | None]:
    """Infer one active query protocol and its evidence summary.

    Existing authored protocols are never overwritten.
    """
    if select.get("option_query"):
        return copy.deepcopy(select.get("option_query")), copy.deepcopy(select.get("option_query_inference"))
    source_reads = _source_reads(select, reads)
    if not source_reads:
        return None, None
    _normalize_source(select, source_reads)

    protocol: dict = {}
    evidence: list[dict] = []
    search, search_meta = _search_inference(source_reads)
    if search and search_meta:
        protocol["search"] = search
        evidence.append(search_meta)

    pagination, response, page_evidence = _pagination_inference(source_reads, search)
    if pagination:
        protocol["pagination"] = pagination
        if response:
            protocol["response"] = response
        evidence.extend(page_evidence)

    excluded: set[tuple[str, tuple[str | int, ...]]] = set()
    if search:
        excluded.add(_slot_signature(search))
    if pagination:
        excluded.add(_slot_signature(pagination))
        if pagination.get("size_path"):
            excluded.add((str(pagination.get("size_location") or pagination.get("location") or ""),
                          tuple(pagination.get("size_path") or [])))

    validation, validation_meta = _validation_inference(select, source_reads, excluded)
    if validation and validation_meta:
        protocol["validation"] = validation
        evidence.append(validation_meta)
        excluded.add(_slot_signature(validation))

    anchor = source_reads[-1][1]
    dependencies, dependency_evidence = _dependency_inference(
        select,
        anchor,
        _field_candidates(selects, samples),
        excluded,
    )
    if dependencies:
        protocol["dependencies"] = dependencies
        evidence.extend(dependency_evidence)

    if not protocol or not evidence:
        return None, None
    confidence = min(float(item.get("confidence") or 0) for item in evidence)
    if confidence < _MIN_AUTO_CONFIDENCE:
        return None, None
    summary = {
        "status": "inferred",
        "confidence": round(confidence, 3),
        "confirmed_by_user": False,
        "evidence": evidence,
        "source_fingerprint": _fingerprint({
            "endpoint": _normalized_endpoint(select.get("source_url")),
            "method": select.get("source_method"),
            "records_path": select.get("source_records_path"),
        }),
    }
    return protocol, summary


def _enrich_selects(original: Callable):
    def wrapped(post_data: str | None, reads: list[dict], samples: dict | None = None) -> list[dict]:
        selects = list(original(post_data, reads, samples) or [])
        for select in selects:
            protocol, inference = infer_option_query(select, reads or [], selects, samples or {})
            if protocol:
                select["option_query"] = protocol
                select["option_query_inference"] = inference
        return selects

    return wrapped


def _preserve_query_metadata(original: Callable):
    def wrapped(req: dict, param_map: dict, base_url: str = "", selects: list[dict] | None = None,
                identity: list[dict] | None = None, typed: dict | None = None):
        compiled = original(req, param_map, base_url, selects, identity, typed)
        if not compiled:
            return compiled
        source_selects = list(selects or [])
        for target in compiled.get("selects") or []:
            target_param = target.get("param")
            target_path = target.get("path") or target.get("array_path")
            match = next((source for source in source_selects if (
                (source.get("param") or param_map.get(source.get("path"))) == target_param
                and (
                    not target_path
                    or source.get("path") == target_path
                    or source.get("array_path") == target_path
                    or _normalized_endpoint(source.get("source_url")) == _normalized_endpoint(target.get("source_url"))
                )
            )), None)
            if not match:
                continue
            for key in _QUERY_META_KEYS:
                if key in match:
                    target[key] = copy.deepcopy(match[key])
            for dependency in (target.get("option_query") or {}).get("dependencies") or []:
                field_path = dependency.get("field_path")
                if field_path and param_map.get(field_path):
                    dependency["field"] = param_map[field_path]
                dependency.pop("field_path", None)
        return compiled

    return wrapped


def _record_ui_evidence(original: Callable):
    def wrapped(self, source, payload: str):  # noqa: ANN001
        try:
            step = json.loads(payload)
        except (TypeError, ValueError):
            step = None
        if isinstance(step, dict) and step.get("op") in {"fill", "select", "pick"} and step.get("value") not in (None, ""):
            events = getattr(self, "_option_ui_evidence", None)
            if events is None:
                events = []
                self._option_ui_evidence = events
            event = {
                "ref": _next_capture_ref(self, "ui"),
                "at_ms": _now_ms(),
                "op": step.get("op"),
                "field": step.get("field") or "",
                "locator": step.get("locator") or "",
                "value": step.get("value"),
            }
            if not events or not (
                events[-1].get("op") == event["op"]
                and events[-1].get("locator") == event["locator"]
                and _scalar_equal(events[-1].get("value"), event["value"])
            ):
                events.append(event)
                del events[:-_MAX_UI_EVENTS]
        return original(self, source, payload)

    return wrapped


def _record_read_evidence(original: Callable):
    async def wrapped(self, response):  # noqa: ANN001
        before = len(getattr(self, "reads", []))
        await original(self, response)
        now = _now_ms()
        reads = getattr(self, "reads", [])
        fresh = reads[before:]
        if not fresh:
            return
        recent = [
            copy.deepcopy(event)
            for event in getattr(self, "_option_ui_evidence", [])
            if 0 <= now - int(event.get("at_ms") or 0) <= _UI_WINDOW_MS
        ][-12:]
        for read in fresh:
            read.setdefault("_capture_ref", _next_capture_ref(self, "read"))
            read.setdefault("_captured_at_ms", now)
            if recent:
                read["_ui_evidence"] = recent

    return wrapped


def _reset_evidence(original: Callable):
    def wrapped(self):
        result = original(self)
        self._option_ui_evidence = []
        self._option_capture_seq = 0
        return result

    return wrapped


def install_option_query_inference_p2() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.execution.page import recorder as recorder_module
    from dano.execution.page import request_capture as rc

    rc.suggest_selects = _enrich_selects(rc.suggest_selects)
    rc.build_api_request = _preserve_query_metadata(rc.build_api_request)

    if not getattr(recorder_module.RecordSession._on_record, "__dano_option_inference_p2__", False):
        patched_record = _record_ui_evidence(recorder_module.RecordSession._on_record)
        patched_record.__dano_option_inference_p2__ = True
        recorder_module.RecordSession._on_record = patched_record
    if not getattr(recorder_module.RecordSession._on_response, "__dano_option_inference_p2__", False):
        patched_response = _record_read_evidence(recorder_module.RecordSession._on_response)
        patched_response.__dano_option_inference_p2__ = True
        recorder_module.RecordSession._on_response = patched_response
    if not getattr(recorder_module.RecordSession.reset, "__dano_option_inference_p2__", False):
        patched_reset = _reset_evidence(recorder_module.RecordSession.reset)
        patched_reset.__dano_option_inference_p2__ = True
        recorder_module.RecordSession.reset = patched_reset

    _INSTALLED = True
