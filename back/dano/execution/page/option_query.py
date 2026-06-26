"""Backend-brokered option query protocol.

The browser/frontend only sends a Skill field, a search string and business context.
Target-system URLs, request bodies, response paths and credentials remain private in
Dano.  The same live source used by execution is queried here, then normalized into a
small public ``label/value`` page.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from urllib.parse import parse_qsl, urlencode
from typing import Any

OPTION_QUERY_VERSION = "option-query/v1"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
MAX_CONTEXT_FIELDS = 64
MAX_CONTEXT_BYTES = 16 * 1024
MAX_OFFSET = 10_000
MAX_SOURCE_PAGES = 4


def _requests(api_request: dict) -> list[dict]:
    steps = list((api_request or {}).get("steps") or [])
    return steps or [api_request or {}]


def _find_select_request(api_request: dict, field: str) -> tuple[dict | None, dict | None]:
    for request in _requests(api_request):
        for select in request.get("selects") or []:
            if isinstance(select, dict) and select.get("param") == field:
                return select, request
    return None, None


def _context_get(context: dict, path: str):
    current: Any = context
    for token in [p for p in str(path or "").split(".") if p]:
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def _binding_value(binding: dict, *, query: str, context: dict, limit: int, offset: int):
    source = str(binding.get("from") or "")
    if source == "query":
        return query
    if source == "limit":
        return limit
    if source == "offset":
        return offset
    if source == "page":
        return int(binding.get("page_base", 1)) + (offset // max(limit, 1))
    if source == "const":
        return binding.get("value", binding.get("default"))
    if source.startswith("context."):
        return _context_get(context, source[len("context."):])
    return None


def _coerce_binding_value(value, declared: str | None):
    kind = str(declared or "raw").lower()
    if kind in {"", "raw", "any"}:
        return value, None
    try:
        if kind == "string":
            return str(value), None
        if kind == "integer":
            if isinstance(value, bool):
                raise ValueError("boolean is not integer")
            return int(value), None
        if kind == "number":
            if isinstance(value, bool):
                raise ValueError("boolean is not number")
            return float(value), None
        if kind == "boolean":
            if isinstance(value, bool):
                return value, None
            normalized = str(value).strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True, None
            if normalized in {"0", "false", "no", "off"}:
                return False, None
            raise ValueError("not a boolean")
        if kind == "array":
            if isinstance(value, (list, tuple, set)):
                return list(value), None
            raise ValueError("not an array")
        if kind == "object":
            if isinstance(value, dict):
                return value, None
            raise ValueError("not an object")
        return None, f"不支持的绑定类型：{kind}"
    except (TypeError, ValueError):
        return None, f"无法把绑定值转换为 {kind}"


def _tokens(binding: dict) -> list[str | int]:
    tokens = binding.get("tokens")
    if isinstance(tokens, list):
        return list(tokens)
    path = str(binding.get("path") or "")
    if not path:
        return []
    out: list[str | int] = []
    try:
        for segment in path.split("."):
            if not segment:
                continue
            if "[" not in segment:
                out.append(segment)
                continue
            head, *indices = segment.split("[")
            if head:
                out.append(head)
            for index in indices:
                out.append(int(index.rstrip("]")))
    except (TypeError, ValueError):
        return []
    return out


def _set_tokens(root, tokens: list[str | int], value) -> bool:
    if not tokens:
        return False
    current = root
    for index, token in enumerate(tokens[:-1]):
        next_token = tokens[index + 1]
        if isinstance(token, int):
            if not isinstance(current, list) or token < 0:
                return False
            while len(current) <= token:
                current.append([] if isinstance(next_token, int) else {})
            if not isinstance(current[token], (dict, list)):
                current[token] = [] if isinstance(next_token, int) else {}
            current = current[token]
        else:
            if not isinstance(current, dict):
                return False
            if not isinstance(current.get(token), (dict, list)):
                current[token] = [] if isinstance(next_token, int) else {}
            current = current[token]
    last = tokens[-1]
    if isinstance(last, int):
        if not isinstance(current, list) or last < 0:
            return False
        while len(current) <= last:
            current.append(None)
        current[last] = value
        return True
    if not isinstance(current, dict):
        return False
    current[last] = value
    return True


def _body_object(select: dict):
    raw = select.get("source_post_data")
    content_type = str(select.get("source_content_type") or "").lower()
    if isinstance(raw, (dict, list)):
        return copy.deepcopy(raw), "object"
    if raw in (None, ""):
        return {}, "json"
    if "form-urlencoded" in content_type:
        return dict(parse_qsl(str(raw), keep_blank_values=True)), "form"
    try:
        return json.loads(str(raw)), "json"
    except Exception:  # noqa: BLE001
        return None, "raw"


def _apply_source_bindings(select: dict, *, query: str, context: dict,
                           limit: int, offset: int) -> tuple[dict, list[str], bool, bool, list[str]]:
    """Apply typed, allow-listed bindings without exposing the target request."""
    bound = copy.deepcopy(select)
    bindings = list(bound.get("source_input_bindings") or [])
    missing: list[str] = []
    errors: list[str] = []
    used_query = False
    used_pagination = False
    body, body_kind = _body_object(bound)
    query_params = copy.deepcopy(bound.get("source_query") or {})

    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            errors.append(f"绑定 {index + 1} 不是对象")
            continue
        source = str(binding.get("from") or "")
        if source not in {"query", "limit", "offset", "page", "const"} and not source.startswith("context."):
            errors.append(f"绑定 {index + 1} 的来源不受支持")
            continue
        value = _binding_value(binding, query=query, context=context, limit=limit, offset=offset)
        if source.startswith("context.") and value in (None, "") and binding.get("required", True):
            missing.append(source[len("context."):])
            continue
        if value is None:
            continue
        value, conversion_error = _coerce_binding_value(value, binding.get("value_type"))
        if conversion_error:
            errors.append(f"绑定 {index + 1}：{conversion_error}")
            continue
        if source == "query":
            used_query = True
        if source in {"limit", "offset", "page"}:
            used_pagination = True
        target = str(binding.get("target") or "query")
        tokens = _tokens(binding)
        if not tokens:
            errors.append(f"绑定 {index + 1} 缺少有效目标路径")
            continue
        if target == "query":
            if not _set_tokens(query_params, tokens, value):
                errors.append(f"绑定 {index + 1} 无法写入查询参数")
        elif target == "body":
            if body is None or body_kind == "raw" or not _set_tokens(body, tokens, value):
                errors.append(f"绑定 {index + 1} 无法写入请求体")
        else:
            errors.append(f"绑定 {index + 1} 的目标不受支持")

    if query_params:
        bound["source_query"] = query_params
    if body is not None and body_kind != "raw":
        if body_kind == "form":
            bound["source_post_data"] = urlencode(body, doseq=True)
        else:
            bound["source_post_data"] = body
    return (bound, list(dict.fromkeys(missing)), used_query, used_pagination,
            list(dict.fromkeys(errors)))


def _normalize_option(item, *, label_key: str | None, value_key: str | None) -> dict | None:
    from dano.execution.page import request_capture as rc

    if isinstance(item, dict):
        if "label" in item and "value" in item:
            label = str(item.get("label") or "").strip()
            value = rc._option_value(item.get("value"))
        else:
            label = str(item.get(label_key, "")).strip() if label_key else ""
            value = rc._option_value(item.get(value_key)) if value_key else ""
        if not label:
            return None
        return {"label": label, "value": value}
    if item in (None, ""):
        return None
    return {"label": str(item), "value": rc._option_value(item)}


def _normalize_options(items: list, select: dict) -> list[dict]:
    label_key, value_key = select.get("label_key"), select.get("value_key")
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        option = _normalize_option(item, label_key=label_key, value_key=value_key)
        if option is None:
            continue
        key = (option["label"], str(option["value"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(option)
    return out


def _filter_options(options: list[dict], query: str) -> list[dict]:
    q = str(query or "").strip().casefold()
    if not q:
        return options
    return [
        option for option in options
        if q in str(option.get("label") or "").casefold()
        or q in str(option.get("value") or "").casefold()
    ]


def _fingerprint(query: str, context: dict) -> str:
    raw = json.dumps({"q": query or "", "c": context or {}}, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _encode_cursor(offset: int, fingerprint: str) -> str:
    raw = json.dumps({"o": offset, "f": fingerprint}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, fingerprint: str) -> tuple[int, bool]:
    if not cursor:
        return 0, True
    try:
        padded = str(cursor) + "=" * (-len(str(cursor)) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        offset = int(payload.get("o", 0))
        return (offset, 0 <= offset <= MAX_OFFSET and payload.get("f") == fingerprint)
    except Exception:  # noqa: BLE001
        return 0, False


def _response(field: str, *, status: str, options: list[dict] | None = None,
              total: int = 0, returned: int | None = None, submit_mode: str = "value",
              has_more: bool = False, next_cursor: str | None = None,
              note: str | None = None, http_status: int | None = None,
              dependencies: list[str] | None = None, count_exact: bool = True) -> dict:
    out = {
        "protocol_version": OPTION_QUERY_VERSION,
        "field": field,
        "options": list(options or []),
        "count": total,
        "count_exact": count_exact,
        "returned": len(options or []) if returned is None else returned,
        "submit_mode": submit_mode,
        "source_status": status,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }
    if note:
        out["note"] = note
    if http_status is not None:
        out["http_status"] = http_status
    if dependencies:
        out["dependencies"] = dependencies
    return out


async def query_field_options(api_request: dict, field: str, *, base_url: str = "",
                              storage_state=None, token_key: str | None = None,
                              verify: bool = True, query: str = "", context: dict | None = None,
                              limit: int = DEFAULT_LIMIT, cursor: str | None = None) -> dict:
    """Return one public option page while keeping target-system details private."""
    from dano.execution.page import option_p0

    context = dict(context or {})
    try:
        context_bytes = len(json.dumps(context, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:  # noqa: BLE001
        return _response(field, status="invalid_context", note="业务上下文无法序列化")
    if len(context) > MAX_CONTEXT_FIELDS or context_bytes > MAX_CONTEXT_BYTES:
        return _response(field, status="invalid_context", note="业务上下文过大，请只提交候选依赖字段")

    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    fingerprint = _fingerprint(query, context)
    offset, cursor_ok = _decode_cursor(cursor, fingerprint)
    if not cursor_ok:
        return _response(field, status="invalid_cursor", note="候选游标已失效，请重新加载")

    select, request = _find_select_request(api_request or {}, field)
    if not select:
        return _response(field, status="not_dynamic", note="该字段不是选择字段")
    submit_mode = select.get("submit_mode") or ("value[]" if select.get("kind") == "array" else "value")
    dependencies = sorted({
        str(binding.get("from"))[len("context."):]
        for binding in (select.get("source_input_bindings") or [])
        if isinstance(binding, dict) and str(binding.get("from") or "").startswith("context.")
    })
    request_headers = {
        **dict((api_request or {}).get("auth_headers") or {}),
        **dict((request or {}).get("auth_headers") or {}),
    }

    async def fetch_page(page_offset: int):
        bound, missing, used_query, used_pagination, binding_errors = _apply_source_bindings(
            select, query=query, context=context, limit=limit, offset=page_offset)
        if missing:
            return None, _response(
                field, status="needs_context", submit_mode=submit_mode, dependencies=missing,
                note="请先填写依赖字段：" + "、".join(missing))
        if binding_errors:
            return None, _response(
                field, status="invalid_binding", submit_mode=submit_mode, dependencies=dependencies,
                note="；".join(binding_errors))
        if bound.get("source_url"):
            items, source = await option_p0._fetch_options(
                bound, base_url=base_url, storage_state=storage_state,
                token_key=token_key, verify=verify, auth_headers=request_headers)
            if not source.get("ok"):
                return None, _response(
                    field, status=str(source.get("source_status") or "source_error"),
                    submit_mode=submit_mode, note=str(source.get("message") or "候选来源不可用"),
                    http_status=int(source.get("status") or 0), dependencies=dependencies)
            raw_count = int(source.get("raw_count", len(items)))
        else:
            items = list(bound.get("options") or [])
            raw_count = len(items)
        return {
            "bound": bound,
            "items": items,
            "raw_count": max(raw_count, 0),
            "used_query": used_query,
            "used_pagination": used_pagination,
        }, None

    first, error = await fetch_page(offset)
    if error:
        return error
    assert first is not None

    if not first["used_pagination"]:
        options = _normalize_options(first["items"], first["bound"])
        filtered = _filter_options(options, query)
        total = len(filtered)
        page = filtered[offset:offset + limit]
        next_offset = offset + len(page)
        has_more = next_offset < total and next_offset <= MAX_OFFSET
        note = "已在 Dano 内部筛选候选项" if query and not first["used_query"] else None
        if not page:
            note = "当前条件下没有可选项"
        return _response(
            field, status="ok" if page else "empty", options=page, total=total,
            submit_mode=submit_mode, has_more=has_more,
            next_cursor=_encode_cursor(next_offset, fingerprint) if has_more else None,
            note=note, dependencies=dependencies, count_exact=True)

    collected: list[dict] = []
    seen: set[tuple[str, str]] = set()
    consumed = 0
    last_raw_count = 0
    used_query = bool(first["used_query"])
    current = first
    attempts = 0
    while attempts < MAX_SOURCE_PAGES:
        attempts += 1
        normalized = _normalize_options(current["items"], current["bound"])
        for option in _filter_options(normalized, query):
            key = (str(option.get("label") or ""), str(option.get("value") or ""))
            if key not in seen:
                seen.add(key)
                collected.append(option)
        last_raw_count = int(current["raw_count"])
        consumed += last_raw_count
        # Do not consume a second upstream page once this response has matches:
        # advancing past a partly-used page would silently drop candidates. Empty pages
        # may be skipped (bounded) so local filtering can still find a visible result.
        if collected or last_raw_count < limit or last_raw_count == 0:
            break
        next_page_offset = offset + consumed
        if next_page_offset > MAX_OFFSET:
            break
        current, error = await fetch_page(next_page_offset)
        if error:
            return error
        assert current is not None
        used_query = used_query or bool(current["used_query"])

    page = collected[:limit]
    next_offset = offset + consumed
    has_more = last_raw_count >= limit and next_offset <= MAX_OFFSET
    note = None
    if not page:
        note = "当前条件下没有可选项"
    elif query and not used_query:
        note = "已在 Dano 内部筛选候选项"
    if next_offset > MAX_OFFSET:
        has_more = False
        note = "已达到候选浏览上限，请使用更精确的搜索词"
    return _response(
        field, status="ok" if page else "empty", options=page,
        total=next_offset + (1 if has_more else 0), submit_mode=submit_mode,
        has_more=has_more, next_cursor=_encode_cursor(next_offset, fingerprint) if has_more else None,
        note=note, dependencies=dependencies, count_exact=not has_more)
