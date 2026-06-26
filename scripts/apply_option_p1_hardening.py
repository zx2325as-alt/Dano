from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, new: str, label: str) -> str:
    left = text.find(start)
    if left < 0:
        raise RuntimeError(f"{label}: start not found")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"{label}: end not found")
    return text[:left] + new + text[right:]


def patch_option_query() -> None:
    path = "back/dano/execution/page/option_query.py"
    text = read(path)
    text = replace_once(
        text,
        'MAX_LIMIT = 100\n',
        'MAX_LIMIT = 100\nMAX_CONTEXT_FIELDS = 64\nMAX_CONTEXT_BYTES = 16 * 1024\nMAX_OFFSET = 10_000\nMAX_SOURCE_PAGES = 4\n',
        "query limits",
    )
    start = 'def _binding_value(binding: dict, *, query: str, context: dict, limit: int, offset: int):\n'
    end = '\n\ndef _normalize_option(item, *, label_key: str | None, value_key: str | None) -> dict | None:\n'
    block = '''def _binding_value(binding: dict, *, query: str, context: dict, limit: int, offset: int):
    source = str(binding.get("from") or "")
    if source == "query":
        return query
    if source == "limit":
        return limit
    if source == "offset":
        return offset
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
        if source not in {"query", "limit", "offset", "const"} and not source.startswith("context."):
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
        if source in {"limit", "offset"}:
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
'''
    text = replace_between(text, start, end, block, "binding implementation")

    text = replace_once(
        text,
        '        return (offset, offset >= 0 and payload.get("f") == fingerprint)\n',
        '        return (offset, 0 <= offset <= MAX_OFFSET and payload.get("f") == fingerprint)\n',
        "cursor offset bound",
    )
    text = replace_once(
        text,
        '              dependencies: list[str] | None = None) -> dict:\n',
        '              dependencies: list[str] | None = None, count_exact: bool = True) -> dict:\n',
        "response signature",
    )
    text = replace_once(
        text,
        '        "count": total,\n        "returned": len(options or []) if returned is None else returned,\n',
        '        "count": total,\n        "count_exact": count_exact,\n        "returned": len(options or []) if returned is None else returned,\n',
        "response count exact",
    )

    query_start = 'async def query_field_options(api_request: dict, field: str, *, base_url: str = "",\n'
    left = text.find(query_start)
    if left < 0:
        raise RuntimeError("query_field_options start not found")
    query_impl = '''async def query_field_options(api_request: dict, field: str, *, base_url: str = "",
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
        if len(collected) >= limit or last_raw_count < limit or last_raw_count == 0:
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
'''
    text = text[:left] + query_impl
    write(path, text)


def patch_option_p0() -> None:
    path = "back/dano/execution/page/option_p0.py"
    text = read(path)
    old = '''    items = _extract_option_items(data, _source_spec(sel)["records_path"])
    if items is None:
        return [], {
            "ok": False,
            "status": status["status"],
            "source_status": "invalid_shape",
            "message": "候选来源响应结构已变化，无法定位候选列表",
        }
    items = rc._apply_option_filter(items, sel.get("option_filter"))
    return items, status
'''
    new = '''    items = _extract_option_items(data, _source_spec(sel)["records_path"])
    if items is None:
        return [], {
            "ok": False,
            "status": status["status"],
            "source_status": "invalid_shape",
            "message": "候选来源响应结构已变化，无法定位候选列表",
        }
    status["raw_count"] = len(items)
    items = rc._apply_option_filter(items, sel.get("option_filter"))
    return items, status
'''
    text = replace_once(text, old, new, "option raw count")
    write(path, text)


def patch_gateway() -> None:
    path = "back/dano/gateway/app.py"
    text = read(path)
    text = replace_once(
        text,
        '''class ToolOptionsReq(BaseModel):
    name: str                       # 工具名(= skill_id 点转 __)
    field: str                      # 要列可选项的业务参数名
''',
        '''class ToolOptionsReq(BaseModel):
    name: str = Field(min_length=1, max_length=300)   # 工具名(= skill_id 点转 __)
    field: str = Field(min_length=1, max_length=200)  # 要列可选项的业务参数名
''',
        "gateway option field limits",
    )
    text = replace_once(
        text,
        '''    orch = await _orchestrator(tenant)
    return await orch.list_field_options(
        Subsystem(sub_str), action, req.field, tenant=tenant,
''',
        '''    try:
        subsystem = Subsystem(sub_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"未知 subsystem: {sub_str}") from exc
    orch = await _orchestrator(tenant)
    return await orch.list_field_options(
        subsystem, action, req.field, tenant=tenant,
''',
        "gateway subsystem validation",
    )
    write(path, text)


def patch_frontend() -> None:
    path = "skillfrontend/src/components/InvokeDrawer.tsx"
    text = read(path)
    old = '''  const setVal = (key: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    const dependents = Object.entries(props)
      .filter(([field, prop]) => field !== key && (prop?.["x-option-depends-on"] || []).includes(key))
      .map(([field]) => field);
    clearOptionFields(dependents);
  };
'''
    new = '''  const dependentOptionFields = (changed: string): string[] => {
    const result = new Set<string>();
    const queue = [changed];
    while (queue.length) {
      const current = queue.shift() as string;
      for (const [field, prop] of Object.entries(props)) {
        if (field === changed || result.has(field)) continue;
        if ((prop?.["x-option-depends-on"] || []).includes(current)) {
          result.add(field);
          queue.push(field);
        }
      }
    }
    return [...result];
  };

  const setVal = (key: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    clearOptionFields(dependentOptionFields(key));
  };
'''
    text = replace_once(text, old, new, "transitive cascade invalidation")
    text = replace_once(
        text,
        '    const label = p.description || key;\n',
        '    const label = p.label || p.description || key;\n',
        "field label priority",
    )
    write(path, text)


def patch_frontend_types() -> None:
    path = "skillfrontend/src/api/skills.ts"
    text = read(path)
    text = replace_once(
        text,
        '  | "invalid_context"\n' if '  | "invalid_context"\n' in text else '  | "invalid_request"\n',
        '  | "invalid_context"\n  | "invalid_binding"\n  | "invalid_request"\n',
        "option status types",
    )
    text = replace_once(
        text,
        '  count: number;\n  options: ToolOption[];\n',
        '  count: number;\n  count_exact?: boolean;\n  options: ToolOption[];\n',
        "count exact type",
    )
    write(path, text)


def main() -> None:
    patch_option_query()
    patch_option_p0()
    patch_gateway()
    patch_frontend()
    patch_frontend_types()


if __name__ == "__main__":
    main()
