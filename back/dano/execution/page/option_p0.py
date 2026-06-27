"""P0 dynamic option hardening.

The legacy request skill remains executable, but dynamic option sources are replayed
with their recorded request shape and must be verified live before a write is sent.
This module is an additive compatibility layer so older GET-only assets keep working.
"""
from __future__ import annotations

import copy
import json
from urllib.parse import parse_qsl, urlparse

_INSTALLED = False
_ALLOWED_SOURCE_METHODS = {"GET", "POST", "PUT", "PATCH"}
_SOURCE_META_KEYS = (
    "source_method",
    "source_post_data",
    "source_content_type",
    "source_query",
    "source_headers",
    "source_records_path",
    "primitive",
)
_SENSITIVE_HEADER_PARTS = (
    "authorization",
    "proxy-authorization",
    "cookie",
    "token",
    "secret",
    "api-key",
    "apikey",
    "credential",
    "session",
    "satoken",
)


def _source_error(status: int) -> tuple[str, str]:
    if status == 400:
        return "invalid_request", "候选来源请求条件无效"
    if status == 401:
        return "auth_expired", "登录态已失效，请刷新登录态后重试"
    if status == 403:
        return "permission_denied", "当前账号没有读取候选项的权限"
    if status == 404:
        return "source_not_found", "候选来源接口不存在或已变更"
    if status == 409:
        return "source_conflict", "候选来源当前状态不允许查询"
    if status == 422:
        return "invalid_request", "候选来源缺少必要查询条件"
    if status == 429:
        return "rate_limited", "候选来源请求过于频繁，请稍后重试"
    if status >= 500:
        return "source_unavailable", "候选来源服务暂时不可用"
    return "source_error", f"候选来源请求失败（HTTP {status}）"


def _is_sensitive_header(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    return any(part in normalized for part in _SENSITIVE_HEADER_PARTS)


def _safe_source_headers(headers: dict | None) -> dict[str, str]:
    """Keep non-secret request context such as tenant/app headers.

    Credentials must come from the current runtime token/vault, never from a recorded
    option response embedded in the Skill asset.
    """
    from dano.execution.page import request_capture as rc

    return {
        str(key): str(value)
        for key, value in rc.extract_auth_headers(headers or {}).items()
        if value not in (None, "") and not _is_sensitive_header(str(key))
    }


def _source_spec(sel: dict) -> dict:
    method = str(sel.get("source_method") or "GET").upper()
    records_path = sel.get("source_records_path") if "source_records_path" in sel else None
    return {
        "method": method,
        "url": sel.get("source_url") or "",
        "post_data": sel.get("source_post_data"),
        "content_type": sel.get("source_content_type") or "application/json",
        "query": sel.get("source_query") or {},
        "headers": sel.get("source_headers") or {},
        "records_path": records_path,
    }


def _find_list_path(data) -> list[str | int] | None:
    """Find an option-list path, including a legitimate empty list."""
    from dano.execution.page import request_capture as rc

    if isinstance(data, list):
        return []
    if not isinstance(data, dict):
        return None
    for key in rc._LIST_KEYS:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, list):
            return [key]
        if isinstance(value, dict):
            for child_key in rc._LIST_KEYS:
                child = value.get(child_key)
                if isinstance(child, list):
                    return [key, child_key]
    for key, value in data.items():
        if isinstance(value, list) and (not value or isinstance(value[0], (dict, str, int, float, bool))):
            return [key]
        if isinstance(value, dict):
            for child_key, child in value.items():
                if isinstance(child, list) and (not child or isinstance(child[0], (dict, str, int, float, bool))):
                    return [key, child_key]
    return None


def _get_path(data, path: list[str | int]):
    current = data
    for token in path:
        if isinstance(token, int):
            if not isinstance(current, list) or token < 0 or token >= len(current):
                return None
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return None
            current = current[token]
    return current


def _extract_option_items(data, records_path: list[str | int] | None) -> list | None:
    """Extract options without confusing an empty result with response-shape drift."""
    if records_path is not None:
        value = _get_path(data, records_path)
        return value if isinstance(value, list) else None
    path = _find_list_path(data)
    if path is None:
        return None
    value = _get_path(data, path)
    return value if isinstance(value, list) else None


def _has_header(headers: dict, name: str) -> bool:
    expected = name.lower()
    return any(str(key).lower() == expected for key in headers)


async def _request_source_json(
    sel: dict,
    *,
    base_url: str,
    storage_state,
    token_key: str | None,
    verify: bool,
    auth_headers: dict | None,
) -> tuple[object | None, dict]:
    """Replay an option source using its recorded method/body and current credentials."""
    from dano.execution.page import request_capture as rc

    spec = _source_spec(sel)
    method = spec["method"]
    if method not in _ALLOWED_SOURCE_METHODS:
        return None, {
            "ok": False,
            "status": 0,
            "source_status": "unsupported_method",
            "message": f"不支持使用 {method} 查询候选项",
        }

    raw_url = spec["url"]
    full = raw_url if raw_url.startswith("http") else (base_url or "").rstrip("/") + raw_url
    host = urlparse(full).hostname or ""
    # Recorded context headers contain no credential. Current runtime headers always win.
    headers = {**spec["headers"], **(auth_headers or {})}
    session_headers = rc._auth_headers(storage_state, host, token_key)
    if session_headers.get("Cookie"):
        headers["Cookie"] = session_headers["Cookie"]
    if not _has_header(headers, "Authorization") and session_headers.get("Authorization"):
        headers["Authorization"] = session_headers["Authorization"]

    kwargs: dict = {}
    if method == "GET":
        if spec["query"]:
            kwargs["params"] = spec["query"]
    else:
        post_data = spec["post_data"]
        content_type = spec["content_type"]
        if content_type:
            headers.setdefault("Content-Type", content_type)
        if post_data not in (None, ""):
            if isinstance(post_data, (dict, list)):
                kwargs["json"] = post_data
            elif "form-urlencoded" in content_type.lower():
                kwargs["data"] = dict(parse_qsl(str(post_data), keep_blank_values=True))
            else:
                try:
                    kwargs["json"] = json.loads(str(post_data))
                except Exception:  # noqa: BLE001
                    kwargs["content"] = str(post_data)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=30, verify=verify) as client:
            response = await client.request(method, full, headers=headers, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return None, {
            "ok": False,
            "status": 0,
            "source_status": "network_error",
            "message": f"候选来源网络异常：{exc}",
        }

    if response.status_code < 200 or response.status_code >= 300:
        source_status, message = _source_error(response.status_code)
        return None, {
            "ok": False,
            "status": response.status_code,
            "source_status": source_status,
            "message": message,
        }
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return None, {
            "ok": False,
            "status": response.status_code,
            "source_status": "invalid_response",
            "message": "候选来源返回的不是合法 JSON",
        }
    return data, {"ok": True, "status": response.status_code, "source_status": "ok", "message": ""}


def _primitive_select_candidates(post_data: str | None, reads: list[dict], samples: dict | None,
                                 occupied_paths: set[str]) -> list[dict]:
    """Safely recognize small primitive-string enums omitted by the legacy detector.

    Numeric/status-like values are deliberately not guessed without direct UI evidence;
    this prevents values such as ``1`` from binding to an unrelated status dictionary.
    """
    from dano.execution.page import request_capture as rc

    body = rc._parse_body(post_data)
    if body is None:
        return []
    sample_values = {str(v) for v in (samples or {}).values() if v not in (None, "")}
    candidates: dict[str, list[tuple[int, dict]]] = {}
    for read_index, read in enumerate(reads or []):
        items = rc.as_list_payload(read.get("json"))
        if not items or len(items) > rc._SMALL_LIST or not all(
            not isinstance(item, (dict, list)) for item in items
        ):
            continue
        normalized = {str(item): item for item in items}
        for path, tokens, value, raw in rc._leaf_paths(body):
            if path in occupied_paths or value not in normalized or rc._is_const_value(raw):
                continue
            direct_ui_evidence = value in sample_values
            safe_text_enum = isinstance(raw, str) and len(value.strip()) >= 2
            if not direct_ui_evidence and not safe_text_enum:
                continue
            score = (100 if direct_ui_evidence else 30) + max(0, 20 - len(items)) + read_index
            options = [{"label": str(item), "value": rc._option_value(item)} for item in items]
            entry = {
                "path": path,
                "tokens": tokens,
                "value": value,
                "source_url": read.get("url"),
                "value_key": None,
                "label_key": None,
                "label": str(normalized[value]),
                "count": len(items),
                "options": options,
                "source_keys": [],
                "submit_mode": "value",
                "primitive": True,
                "_confirmed": direct_ui_evidence,
            }
            candidates.setdefault(path, []).append((score, entry))

    out: list[dict] = []
    for path, ranked in candidates.items():
        ranked.sort(key=lambda item: item[0], reverse=True)
        # Ambiguous same-score sources are not auto-bound.
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 3:
            continue
        out.append(ranked[0][1])
    return out


def _apply_read_metadata(select: dict, read: dict) -> None:
    select["source_method"] = str(read.get("method") or "GET").upper()
    if read.get("post_data") not in (None, ""):
        select["source_post_data"] = read.get("post_data")
    if read.get("content_type"):
        select["source_content_type"] = read.get("content_type")
    if read.get("source_headers"):
        select["source_headers"] = copy.deepcopy(read.get("source_headers"))
    if "records_path" in read:
        select["source_records_path"] = copy.deepcopy(read.get("records_path"))
    else:
        records_path = _find_list_path(read.get("json"))
        if records_path is not None:
            select["source_records_path"] = records_path


def _enrich_select_sources(original):
    def wrapped(post_data: str | None, reads: list[dict], samples: dict | None = None) -> list[dict]:
        selects = list(original(post_data, reads, samples) or [])
        occupied = {str(item.get("path")) for item in selects if item.get("path")}
        selects.extend(_primitive_select_candidates(post_data, reads, samples, occupied))

        by_url: dict[str, list[dict]] = {}
        for read in reads or []:
            by_url.setdefault(str(read.get("url") or ""), []).append(read)
        for select in selects:
            matches = by_url.get(str(select.get("source_url") or "")) or []
            if matches:
                _apply_read_metadata(select, matches[-1])
        return selects

    return wrapped


def _preserve_compiled_source_metadata(original):
    """The legacy compiler dropped method/body metadata for scalar selects."""

    def wrapped(req: dict, param_map: dict, base_url: str = "", selects: list[dict] | None = None,
                identity: list[dict] | None = None, typed: dict | None = None):
        compiled = original(req, param_map, base_url, selects, identity, typed)
        if not compiled:
            return compiled
        source_selects = list(selects or [])
        for target in compiled.get("selects") or []:
            target_param = target.get("param")
            target_path = target.get("path") or target.get("array_path")
            match = next(
                (
                    source
                    for source in source_selects
                    if (
                        (source.get("param") or param_map.get(source.get("path"))) == target_param
                        and (
                            not target_path
                            or source.get("path") == target_path
                            or source.get("array_path") == target_path
                            or source.get("source_url") == target.get("source_url")
                        )
                    )
                ),
                None,
            )
            if not match:
                continue
            for key in _SOURCE_META_KEYS:
                if key in match:
                    target[key] = copy.deepcopy(match[key])
        return compiled

    return wrapped


def _capture_read_request_metadata(original):
    async def wrapped(self, response) -> None:  # noqa: ANN001
        before = len(self.reads)
        await original(self, response)
        try:
            from dano.execution.page import request_capture as rc

            request = response.request
            method = (request.method or "").upper()
            if method not in _ALLOWED_SOURCE_METHODS:
                return
            url = response.url
            request_headers = dict(request.headers or {})
            post_data = request.post_data if method in {"POST", "PUT", "PATCH"} else None
            match = next(
                (
                    read
                    for read in reversed(self.reads[before:])
                    if read.get("method") == method and read.get("url") == url
                ),
                None,
            )
            if match is None:
                match = next(
                    (
                        read
                        for read in reversed(self.reads)
                        if read.get("method") == method and read.get("url") == url
                    ),
                    None,
                )

            if match is None:
                response_type = str((response.headers or {}).get("content-type", "")).lower()
                if "json" not in response_type or any(noise in url.lower() for noise in rc._READ_NOISE):
                    return
                data = await response.json()
                records_path = _find_list_path(data)
                if records_path is None:
                    return
                items = _get_path(data, records_path)
                match = {
                    "method": method,
                    "url": url,
                    "status": response.status,
                    "json": data if len(self.reads) < 60 else None,
                    "count": len(items) if isinstance(items, list) else 0,
                }
                self.reads.append(match)

            match["post_data"] = post_data
            match["content_type"] = request_headers.get("content-type", "")
            match["source_headers"] = _safe_source_headers(request_headers)
            if "records_path" not in match:
                path = _find_list_path(match.get("json"))
                if path is not None:
                    match["records_path"] = path
        except Exception:  # noqa: BLE001
            pass

    return wrapped


async def _fetch_options(sel: dict, *, base_url: str, storage_state, token_key: str | None,
                         verify: bool, auth_headers: dict | None) -> tuple[list, dict]:
    from dano.execution.page import request_capture as rc

    data, status = await _request_source_json(
        sel,
        base_url=base_url,
        storage_state=storage_state,
        token_key=token_key,
        verify=verify,
        auth_headers=auth_headers,
    )
    if not status["ok"]:
        return [], status
    items = _extract_option_items(data, _source_spec(sel)["records_path"])
    if items is None:
        return [], {
            "ok": False,
            "status": status["status"],
            "source_status": "invalid_shape",
            "message": "候选来源响应结构已变化，无法定位候选列表",
        }
    items = rc._apply_option_filter(items, sel.get("option_filter"))
    return items, status


def _option_pair(item, label_key: str | None, value_key: str | None):
    from dano.execution.page import request_capture as rc

    if isinstance(item, dict):
        label = str(item.get(label_key, "")).strip() if label_key else ""
        if not label:
            return None
        return {"label": label, "value": rc._option_value(item.get(value_key))}
    if item in (None, ""):
        return None
    return {"label": str(item), "value": rc._option_value(item)}


async def fetch_field_options(api_request: dict, field: str, *, base_url: str = "",
                              storage_state=None, token_key: str | None = None,
                              verify: bool = True, limit: int = 500) -> dict:
    from dano.execution.page import request_capture as rc

    sel = rc.find_field_select(api_request, field)
    mode = "value[]" if (sel or {}).get("kind") == "array" else "value"
    if not sel or not sel.get("source_url"):
        return {
            "field": field,
            "options": [],
            "count": 0,
            "submit_mode": mode,
            "source_status": "not_dynamic",
            "note": "该字段不是动态选择字段；请按字段说明传值",
        }
    items, status = await _fetch_options(
        sel,
        base_url=base_url,
        storage_state=storage_state,
        token_key=token_key,
        verify=verify,
        auth_headers=(api_request or {}).get("auth_headers"),
    )
    if not status["ok"]:
        return {
            "field": field,
            "options": [],
            "count": 0,
            "submit_mode": mode,
            "source_status": status["source_status"],
            "note": status["message"],
            "http_status": status["status"],
        }

    label_key, value_key = sel.get("label_key"), sel.get("value_key")
    options = []
    for item in items:
        option = _option_pair(item, label_key, value_key)
        if option is not None:
            options.append(option)
        if len(options) >= limit:
            break
    source_status = "ok" if options else "empty"
    out = {
        "field": field,
        "options": options,
        "count": len(items),
        "submit_mode": mode,
        "source_status": source_status,
    }
    if source_status == "empty":
        out["note"] = "当前条件下没有可选项"
    if sel.get("option_filter"):
        out["option_filter"] = sel.get("option_filter")
    return out


def _match_option(items: list, label_key: str | None, value_key: str | None, submitted):
    from dano.execution.page import request_capture as rc

    if isinstance(submitted, dict) and "value" in submitted:
        submitted = submitted.get("value")
    if all(isinstance(item, dict) for item in items):
        return rc._match_select_item(items, label_key, value_key, submitted)
    match = next((item for item in items if not isinstance(item, dict) and str(item) == str(submitted)), None)
    return (match, "value") if match is not None else (None, None)


async def resolve_selects(api_request: dict, fields: dict, *, base_url: str, storage_state,
                          token_key: str | None, verify: bool) -> tuple[dict, dict]:
    """Resolve dynamic values fail-closed with precise source failure messages."""
    from dano.execution.page import request_capture as rc

    id_overrides: dict = {}
    for sel in api_request.get("selects") or []:
        param = sel.get("param")
        if param not in fields or not sel.get("source_url"):
            continue
        items, status = await _fetch_options(
            sel,
            base_url=base_url,
            storage_state=storage_state,
            token_key=token_key,
            verify=verify,
            auth_headers=api_request.get("auth_headers"),
        )
        if not status["ok"]:
            raise ValueError(f"枚举字段 {param} 无法获取候选项：{status['message']}")

        submitted = fields[param]
        label_key, value_key = sel.get("label_key"), sel.get("value_key")
        if sel.get("kind") == "array":
            matches = []
            for value in rc._select_values(submitted):
                match, _ = _match_option(items, label_key, value_key, value)
                if match is None:
                    raise ValueError(f"枚举数组字段 {param} 的值 {value!r} 不在当前候选项中")
                matches.append(match)
            tokens = sel.get("array_tokens") or rc._split_path(
                sel.get("array_path") or sel.get("path", "")
            )
            if all(isinstance(match, dict) for match in matches):
                rebuilt = rc._build_array_select_items(sel, matches)
            elif all(not isinstance(match, dict) for match in matches):
                rebuilt = matches
            else:
                raise ValueError(f"枚举数组字段 {param} 的候选结构不一致")
            id_overrides[tuple(tokens)] = rebuilt
            for derived in sel.get("derived_count_paths") or []:
                derived_tokens = derived.get("tokens") or rc._split_path(derived.get("path", ""))
                id_overrides[tuple(derived_tokens)] = len(rebuilt)
            continue

        match, _ = _match_option(items, label_key, value_key, submitted)
        if match is None:
            raise ValueError(f"枚举字段 {param} 的值 {submitted!r} 不在当前候选项中")
        if not isinstance(match, dict):
            fields[param] = match
            if sel.get("id_tokens") or sel.get("id_path"):
                tokens = sel.get("id_tokens") or rc._split_path(sel.get("id_path", ""))
                id_overrides[tuple(tokens)] = match
            continue
        if sel.get("id_tokens") or sel.get("id_path"):
            if label_key in match:
                fields[param] = match[label_key]
            if value_key in match:
                tokens = sel.get("id_tokens") or rc._split_path(sel.get("id_path", ""))
                id_overrides[tuple(tokens)] = match[value_key]
        elif value_key in match:
            fields[param] = match[value_key]
    return fields, id_overrides


def install_option_p0() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from dano.execution.page import recorder as recorder_module
    from dano.execution.page import request_capture as rc

    rc.suggest_selects = _enrich_select_sources(rc.suggest_selects)
    rc.build_api_request = _preserve_compiled_source_metadata(rc.build_api_request)
    rc.fetch_field_options = fetch_field_options
    rc._resolve_selects = resolve_selects
    if not getattr(recorder_module.RecordSession._on_response, "__dano_option_p0__", False):
        patched = _capture_read_request_metadata(recorder_module.RecordSession._on_response)
        patched.__dano_option_p0__ = True
        recorder_module.RecordSession._on_response = patched
    _INSTALLED = True
