"""P1 typed query protocol for dynamic option sources.

P0 made option replay safe and fail-closed. P1 adds the missing business protocol for
large and cascading candidate sets without allowing free-form expressions or arbitrary
request mutation.

A select may declare::

    option_query = {
        "search": {"location": "json", "path": ["keyword"], "min_length": 1},
        "pagination": {
            "mode": "page", "location": "json", "path": ["pageNo"],
            "size_path": ["pageSize"], "default_size": 30, "max_size": 100,
        },
        "dependencies": [
            {"field": "部门", "location": "json", "path": ["departmentId"],
             "required": True},
        ],
        "response": {
            "next_cursor_path": ["data", "nextPage"],
            "has_more_path": ["data", "hasMore"],
            "total_path": ["data", "total"],
        },
    }

Only tokenized paths are accepted. Query/form bindings are deliberately flat; nested
mutation is allowed only for JSON bodies. No eval, templates or dotted-path parsing are
used.
"""
from __future__ import annotations

import copy
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode

_INSTALLED = False
_MAX_QUERY_LENGTH = 256
_MAX_CURSOR_LENGTH = 512
_MAX_CONTEXT_FIELDS = 100
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 100


class OptionQueryError(ValueError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_status(self) -> dict:
        return {
            "ok": False,
            "status": 0,
            "source_status": self.code,
            "message": self.message,
            **self.details,
        }


def _protocol(select: dict) -> dict:
    value = select.get("option_query") or {}
    if not isinstance(value, dict):
        raise OptionQueryError("invalid_query_protocol", "候选查询协议必须是对象")
    return value


def _tokens(spec: dict, key: str = "path", *, flat: bool = False) -> list[str | int]:
    path = spec.get(key)
    if not isinstance(path, list) or not path:
        raise OptionQueryError("invalid_query_protocol", f"{key} 必须是非空 token 数组")
    out: list[str | int] = []
    for token in path:
        if isinstance(token, bool) or not isinstance(token, (str, int)):
            raise OptionQueryError("invalid_query_protocol", f"{key} 只能包含字符串或整数 token")
        if isinstance(token, int) and (token < 0 or token > 10_000):
            raise OptionQueryError("invalid_query_protocol", f"{key} 数组下标超出安全范围")
        if isinstance(token, str) and (not token or len(token) > 128):
            raise OptionQueryError("invalid_query_protocol", f"{key} 包含无效字段名")
        out.append(token)
    if flat and (len(out) != 1 or not isinstance(out[0], str)):
        raise OptionQueryError("invalid_query_protocol", f"{key} 在 query/form 中只能是单个字符串 token")
    return out


def _get_path(data: Any, path: list[str | int]) -> Any:
    current = data
    for token in path:
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return None
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return None
            current = current[token]
    return current


def _set_path(data: Any, path: list[str | int], value: Any) -> Any:
    if not path:
        return value
    root = copy.deepcopy(data)
    if root is None:
        root = [] if isinstance(path[0], int) else {}
    current = root
    for index, token in enumerate(path[:-1]):
        next_token = path[index + 1]
        expected = [] if isinstance(next_token, int) else {}
        if isinstance(token, int):
            if not isinstance(current, list):
                raise OptionQueryError("invalid_query_protocol", "JSON token 路径与请求体数组结构不匹配")
            while len(current) <= token:
                current.append(None)
            if current[token] is None:
                current[token] = copy.deepcopy(expected)
            elif isinstance(next_token, int) and not isinstance(current[token], list):
                raise OptionQueryError("invalid_query_protocol", "JSON token 路径与请求体数组结构不匹配")
            elif isinstance(next_token, str) and not isinstance(current[token], dict):
                raise OptionQueryError("invalid_query_protocol", "JSON token 路径与请求体对象结构不匹配")
            current = current[token]
        else:
            if not isinstance(current, dict):
                raise OptionQueryError("invalid_query_protocol", "JSON token 路径与请求体对象结构不匹配")
            if token not in current or current[token] is None:
                current[token] = copy.deepcopy(expected)
            elif isinstance(next_token, int) and not isinstance(current[token], list):
                raise OptionQueryError("invalid_query_protocol", "JSON token 路径与请求体数组结构不匹配")
            elif isinstance(next_token, str) and not isinstance(current[token], dict):
                raise OptionQueryError("invalid_query_protocol", "JSON token 路径与请求体对象结构不匹配")
            current = current[token]
    last = path[-1]
    if isinstance(last, int):
        if not isinstance(current, list):
            raise OptionQueryError("invalid_query_protocol", "JSON token 路径与请求体数组结构不匹配")
        while len(current) <= last:
            current.append(None)
        current[last] = value
    else:
        if not isinstance(current, dict):
            raise OptionQueryError("invalid_query_protocol", "JSON token 路径与请求体对象结构不匹配")
        current[last] = value
    return root


def _unwrap_value(value: Any, value_path: list[str | int] | None = None) -> Any:
    if value_path:
        return _get_path(value, value_path)
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _normalize_location(spec: dict, select: dict) -> str:
    location = str(spec.get("location") or "body").lower()
    if location == "body":
        content_type = str(select.get("source_content_type") or "application/json").lower()
        location = "form" if "form-urlencoded" in content_type else "json"
    if location not in {"query", "json", "form"}:
        raise OptionQueryError("invalid_query_protocol", f"不支持候选查询注入位置 {location}")
    return location


def _decode_json_body(select: dict) -> Any:
    body = select.get("source_post_data")
    if body in (None, ""):
        return {}
    if isinstance(body, (dict, list)):
        return copy.deepcopy(body)
    try:
        parsed = json.loads(str(body))
    except Exception as exc:  # noqa: BLE001
        raise OptionQueryError("invalid_query_protocol", "候选来源请求体不是可修改的 JSON") from exc
    if not isinstance(parsed, (dict, list)):
        raise OptionQueryError("invalid_query_protocol", "候选来源 JSON 请求体必须是对象或数组")
    return parsed


def _inject(select: dict, spec: dict, value: Any) -> None:
    location = _normalize_location(spec, select)
    if location == "query":
        path = _tokens(spec, flat=True)
        query = copy.deepcopy(select.get("source_query") or {})
        if not isinstance(query, dict):
            raise OptionQueryError("invalid_query_protocol", "source_query 必须是对象")
        query[path[0]] = value
        select["source_query"] = query
        return
    if location == "form":
        path = _tokens(spec, flat=True)
        body = select.get("source_post_data")
        if isinstance(body, dict):
            form = {str(key): value for key, value in body.items()}
        else:
            form = dict(parse_qsl(str(body or ""), keep_blank_values=True))
        form[path[0]] = value
        select["source_post_data"] = urlencode(form, doseq=True)
        select["source_content_type"] = "application/x-www-form-urlencoded"
        return
    path = _tokens(spec)
    select["source_post_data"] = _set_path(_decode_json_body(select), path, value)
    select["source_content_type"] = "application/json"


def _query_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("label", value.get("value", ""))
    text = str(value).strip()
    if len(text) > _MAX_QUERY_LENGTH:
        raise OptionQueryError("query_too_long", f"搜索词不能超过 {_MAX_QUERY_LENGTH} 个字符")
    return text


def _cursor_value(value: Any) -> str | int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise OptionQueryError("invalid_cursor", "分页游标只能是字符串或整数")
    if isinstance(value, str) and len(value) > _MAX_CURSOR_LENGTH:
        raise OptionQueryError("invalid_cursor", "分页游标过长")
    return value


def _page_size(limit: int | None, pagination: dict | None) -> int:
    pagination = pagination or {}
    default_size = int(pagination.get("default_size") or _DEFAULT_PAGE_SIZE)
    maximum = int(pagination.get("max_size") or _MAX_PAGE_SIZE)
    maximum = max(1, min(maximum, _MAX_PAGE_SIZE))
    requested = default_size if limit is None else int(limit)
    return max(1, min(requested, maximum))


def _prepare_select(
    select: dict,
    *,
    query: Any = None,
    cursor: Any = None,
    limit: int | None = None,
    context: dict | None = None,
    validation: bool = False,
) -> dict:
    prepared = copy.deepcopy(select)
    protocol = _protocol(prepared)
    context = dict(context or {})
    if len(context) > _MAX_CONTEXT_FIELDS:
        raise OptionQueryError("invalid_context", "候选依赖上下文字段过多")

    missing: list[str] = []
    dependencies = protocol.get("dependencies") or []
    if not isinstance(dependencies, list):
        raise OptionQueryError("invalid_query_protocol", "dependencies 必须是数组")
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise OptionQueryError("invalid_query_protocol", "dependency 必须是对象")
        field = str(dependency.get("field") or "").strip()
        if not field:
            raise OptionQueryError("invalid_query_protocol", "dependency.field 不能为空")
        raw = context.get(field)
        if raw in (None, "", []):
            if dependency.get("required", True):
                missing.append(field)
            continue
        value_path = dependency.get("value_path")
        if value_path is not None:
            value_path = _tokens({"path": value_path})
        value = _unwrap_value(raw, value_path)
        if value in (None, "", []):
            if dependency.get("required", True):
                missing.append(field)
            continue
        _inject(prepared, dependency, value)
    if missing:
        unique = list(dict.fromkeys(missing))
        raise OptionQueryError(
            "missing_dependency",
            "请先提供候选字段依赖项：" + "、".join(unique),
            missing_dependencies=unique,
        )

    search = protocol.get("search")
    search_text = _query_text(query)
    if search is not None:
        if not isinstance(search, dict):
            raise OptionQueryError("invalid_query_protocol", "search 必须是对象")
        minimum = max(0, int(search.get("min_length") or 0))
        required = bool(search.get("required", False))
        if search_text:
            if len(search_text) < minimum:
                raise OptionQueryError(
                    "query_too_short",
                    f"搜索词至少需要 {minimum} 个字符",
                    min_query_length=minimum,
                )
            _inject(prepared, search, search_text)
        elif required and not validation:
            raise OptionQueryError(
                "query_required",
                "该候选来源需要搜索词",
                min_query_length=minimum,
            )

    pagination = protocol.get("pagination")
    size = _page_size(limit, pagination if isinstance(pagination, dict) else None)
    current_cursor = _cursor_value(cursor)
    if pagination is not None:
        if not isinstance(pagination, dict):
            raise OptionQueryError("invalid_query_protocol", "pagination 必须是对象")
        mode = str(pagination.get("mode") or "page").lower()
        if mode not in {"page", "offset", "cursor"}:
            raise OptionQueryError("invalid_query_protocol", f"不支持分页模式 {mode}")
        if current_cursor is None:
            current_cursor = pagination.get("start")
            if current_cursor is None and mode == "page":
                current_cursor = 1
            if current_cursor is None and mode == "offset":
                current_cursor = 0
        if current_cursor is not None:
            _inject(prepared, pagination, current_cursor)
        size_path = pagination.get("size_path")
        if size_path:
            size_spec = {
                "location": pagination.get("size_location") or pagination.get("location") or "body",
                "path": size_path,
            }
            _inject(prepared, size_spec, size)

    prepared["_option_query_runtime"] = {
        "query": search_text,
        "cursor": current_cursor,
        "limit": size,
        "validation": validation,
    }
    return prepared


def _response_page_info(select: dict, data: Any) -> dict:
    protocol = _protocol(select)
    response = protocol.get("response") or {}
    pagination = protocol.get("pagination") or {}
    runtime = select.get("_option_query_runtime") or {}
    if not isinstance(response, dict) or not isinstance(pagination, dict):
        return {}

    def read(name: str) -> Any:
        path = response.get(name)
        if not path:
            return None
        return _get_path(data, _tokens({"path": path}))

    next_cursor = read("next_cursor_path")
    has_more = read("has_more_path")
    total = read("total_path")
    if has_more is not None:
        has_more = bool(has_more)
    if total is not None:
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = None

    from dano.execution.page import option_p0

    items = option_p0._extract_option_items(data, option_p0._source_spec(select)["records_path"])
    count = len(items) if isinstance(items, list) else 0
    mode = str(pagination.get("mode") or "page").lower()
    cursor = runtime.get("cursor")
    limit = int(runtime.get("limit") or _DEFAULT_PAGE_SIZE)

    if has_more is None:
        if next_cursor not in (None, ""):
            has_more = True
        elif total is not None and isinstance(cursor, int):
            consumed = (cursor * limit if mode == "page" else cursor + count)
            has_more = consumed < total
        else:
            has_more = count >= limit
    if next_cursor in (None, "") and has_more and isinstance(cursor, int):
        next_cursor = cursor + 1 if mode == "page" else cursor + count

    return {
        "next_cursor": next_cursor,
        "has_more": bool(has_more),
        "total": total,
        "pagination_mode": mode,
    }


def _error_response(field: str, mode: str, error: OptionQueryError) -> dict:
    return {
        "field": field,
        "options": [],
        "count": 0,
        "submit_mode": mode,
        "source_status": error.code,
        "note": error.message,
        **error.details,
    }


def _build_option_response(field: str, mode: str, select: dict, items: list, status: dict, limit: int) -> dict:
    from dano.execution.page import option_p0, option_p0_quality

    if not status.get("ok"):
        return {
            "field": field,
            "options": [],
            "count": 0,
            "submit_mode": mode,
            "source_status": status.get("source_status", "source_error"),
            "note": status.get("message", "候选来源请求失败"),
            "http_status": status.get("status", 0),
            **{key: status[key] for key in ("missing_dependencies", "min_query_length") if key in status},
        }

    label_key, value_key = select.get("label_key"), select.get("value_key")
    options = []
    for item in items:
        option = option_p0._option_pair(item, label_key, value_key)
        if option is not None:
            options.append(option)
    result = {
        "field": field,
        "options": options,
        "count": len(items),
        "submit_mode": mode,
        "source_status": "ok" if options else "empty",
        "search_supported": bool((_protocol(select).get("search"))),
        "depends_on": [
            str(item.get("field"))
            for item in (_protocol(select).get("dependencies") or [])
            if isinstance(item, dict) and item.get("field")
        ],
        **(status.get("page_info") or {}),
    }
    if not options:
        result["note"] = "当前条件下没有可选项"
    return option_p0_quality._normalize_option_result(result, limit)


def _find_select(api_request: dict, field: str) -> dict | None:
    from dano.execution.page import request_capture as rc

    return rc.find_field_select(api_request, field)


def _replace_select(api_request: dict, field: str, prepared: dict) -> dict:
    cloned = copy.deepcopy(api_request)
    target = _find_select(cloned, field)
    if target is None:
        return cloned
    target.clear()
    target.update(prepared)
    return cloned


def _submitted_query(value: Any) -> str:
    if isinstance(value, dict):
        return _query_text(value.get("label", value.get("value")))
    return _query_text(value)


def install_option_query_p1() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.execution.page import option_p0
    from dano.execution.page import request_capture as rc

    original_request_source_json = option_p0._request_source_json

    async def request_source_json_with_page_info(select: dict, **kwargs):
        data, status = await original_request_source_json(select, **kwargs)
        if status.get("ok") and select.get("option_query"):
            try:
                status = {**status, "page_info": _response_page_info(select, data)}
            except OptionQueryError as exc:
                return None, exc.as_status()
        return data, status

    original_fetch_field_options = rc.fetch_field_options

    async def fetch_field_options_with_query(
        api_request: dict,
        field: str,
        *,
        base_url: str = "",
        storage_state=None,
        token_key: str | None = None,
        verify: bool = True,
        limit: int = _DEFAULT_PAGE_SIZE,
        query: Any = None,
        cursor: Any = None,
        context: dict | None = None,
    ) -> dict:
        select = _find_select(api_request, field)
        if not select or not select.get("option_query"):
            return await original_fetch_field_options(
                api_request,
                field,
                base_url=base_url,
                storage_state=storage_state,
                token_key=token_key,
                verify=verify,
                limit=limit,
            )
        mode = "value[]" if select.get("kind") == "array" else "value"
        try:
            prepared = _prepare_select(
                select,
                query=query,
                cursor=cursor,
                limit=limit,
                context=context,
            )
        except OptionQueryError as exc:
            return _error_response(field, mode, exc)
        prepared_api = _replace_select(api_request, field, prepared)
        items, status = await option_p0._fetch_options(
            prepared,
            base_url=base_url,
            storage_state=storage_state,
            token_key=token_key,
            verify=verify,
            auth_headers=(prepared_api or {}).get("auth_headers"),
        )
        return _build_option_response(field, mode, prepared, items, status, _page_size(limit, _protocol(prepared).get("pagination")))

    original_resolve_selects = rc._resolve_selects

    async def resolve_selects_with_query(
        api_request: dict,
        fields: dict,
        *,
        base_url: str,
        storage_state,
        token_key: str | None,
        verify: bool,
    ) -> tuple[dict, dict]:
        if not any(select.get("option_query") for select in api_request.get("selects") or []):
            return await original_resolve_selects(
                api_request,
                fields,
                base_url=base_url,
                storage_state=storage_state,
                token_key=token_key,
                verify=verify,
            )

        resolved_fields = copy.deepcopy(fields)
        id_overrides: dict = {}
        for select in api_request.get("selects") or []:
            param = select.get("param")
            if param not in resolved_fields or not select.get("source_url"):
                continue
            protocol = _protocol(select) if select.get("option_query") else {}
            submitted = resolved_fields[param]
            label_key, value_key = select.get("label_key"), select.get("value_key")
            values = rc._select_values(submitted) if select.get("kind") == "array" else [submitted]
            matches = []
            last_status: dict = {}
            for value in values:
                query_value = _submitted_query(value) if protocol.get("search") else None
                try:
                    prepared = _prepare_select(
                        select,
                        query=query_value,
                        context=resolved_fields,
                        validation=True,
                    )
                except OptionQueryError as exc:
                    raise ValueError(f"枚举字段 {param} 无法验证：{exc.message}") from exc
                items, status = await option_p0._fetch_options(
                    prepared,
                    base_url=base_url,
                    storage_state=storage_state,
                    token_key=token_key,
                    verify=verify,
                    auth_headers=api_request.get("auth_headers"),
                )
                last_status = status
                if not status.get("ok"):
                    raise ValueError(f"枚举字段 {param} 无法获取候选项：{status.get('message')}")
                match, _ = option_p0._match_option(items, label_key, value_key, value)
                if match is None:
                    page_info = status.get("page_info") or {}
                    if protocol.get("pagination") and page_info.get("has_more") and not protocol.get("search"):
                        raise ValueError(
                            f"枚举字段 {param} 使用分页来源但没有搜索协议，无法证明值 {value!r} 有效"
                        )
                    prefix = "枚举数组字段" if select.get("kind") == "array" else "枚举字段"
                    raise ValueError(f"{prefix} {param} 的值 {value!r} 不在当前候选项中")
                matches.append(match)

            if select.get("kind") == "array":
                tokens = select.get("array_tokens") or rc._split_path(select.get("array_path") or select.get("path", ""))
                if all(isinstance(match, dict) for match in matches):
                    rebuilt = rc._build_array_select_items(select, matches)
                elif all(not isinstance(match, dict) for match in matches):
                    rebuilt = matches
                else:
                    raise ValueError(f"枚举数组字段 {param} 的候选结构不一致")
                id_overrides[tuple(tokens)] = rebuilt
                for derived in select.get("derived_count_paths") or []:
                    derived_tokens = derived.get("tokens") or rc._split_path(derived.get("path", ""))
                    id_overrides[tuple(derived_tokens)] = len(rebuilt)
                continue

            match = matches[0]
            if not isinstance(match, dict):
                resolved_fields[param] = match
                if select.get("id_tokens") or select.get("id_path"):
                    tokens = select.get("id_tokens") or rc._split_path(select.get("id_path", ""))
                    id_overrides[tuple(tokens)] = match
                continue
            if select.get("id_tokens") or select.get("id_path"):
                if label_key in match:
                    resolved_fields[param] = match[label_key]
                if value_key in match:
                    tokens = select.get("id_tokens") or rc._split_path(select.get("id_path", ""))
                    id_overrides[tuple(tokens)] = match[value_key]
            elif value_key in match:
                resolved_fields[param] = match[value_key]
        return resolved_fields, id_overrides

    option_p0._request_source_json = request_source_json_with_page_info
    rc.fetch_field_options = fetch_field_options_with_query
    rc._resolve_selects = resolve_selects_with_query
    _INSTALLED = True
