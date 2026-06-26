"""P0 dynamic option hardening.

This module is intentionally additive: it patches the legacy request-capture runtime at
package import time so existing assets keep working while new recordings preserve the
real option-source request and fail closed when live candidates cannot be verified.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qsl, urlparse

_INSTALLED = False


def _source_error(status: int, detail: str = "") -> tuple[str, str]:
    if status == 401:
        return "auth_expired", "登录态已失效，请刷新登录态后重试"
    if status == 403:
        return "permission_denied", "当前账号没有读取候选项的权限"
    if status == 404:
        return "source_not_found", "候选来源接口不存在或已变更"
    if status == 429:
        return "rate_limited", "候选来源请求过于频繁，请稍后重试"
    if status >= 500:
        return "source_unavailable", "候选来源服务暂时不可用"
    return "source_error", detail or (f"候选来源请求失败（HTTP {status}）" if status else "候选来源请求失败")


def _source_spec(sel: dict) -> dict:
    return {
        "method": str(sel.get("source_method") or "GET").upper(),
        "url": sel.get("source_url") or "",
        "post_data": sel.get("source_post_data"),
        "content_type": sel.get("source_content_type") or "application/json",
        "query": sel.get("source_query") or {},
        "auth_headers": sel.get("source_auth_headers") or {},
        "records_path": sel.get("source_records_path") or [],
    }


def _find_list_path(data) -> list[str | int] | None:
    """Find the recorded option-list path, including an empty list when the key is known."""
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
        if isinstance(value, list) and (not value or isinstance(value[0], (dict, str, int, float))):
            return [key]
        if isinstance(value, dict):
            for child_key, child in value.items():
                if isinstance(child, list) and (not child or isinstance(child[0], (dict, str, int, float))):
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
    """Extract options without confusing a legitimate empty list with schema drift."""
    if records_path is not None:
        value = _get_path(data, records_path)
        return value if isinstance(value, list) else None
    path = _find_list_path(data)
    if path is None:
        return None
    value = _get_path(data, path)
    return value if isinstance(value, list) else None


async def _request_source_json(sel: dict, *, base_url: str, storage_state, token_key: str | None,
                               verify: bool, auth_headers: dict | None) -> tuple[object | None, dict]:
    """Replay an option source using its recorded method, body, content type and safe headers."""
    from dano.execution.page import request_capture as rc

    spec = _source_spec(sel)
    raw_url = spec["url"]
    full = raw_url if raw_url.startswith("http") else (base_url or "").rstrip("/") + raw_url
    host = urlparse(full).hostname or ""
    # Source-specific non-browser headers first; the current runtime token/header set wins.
    headers = {**spec["auth_headers"], **(auth_headers or {})}
    session_headers = rc._auth_headers(storage_state, host, token_key)
    if session_headers.get("Cookie"):
        headers["Cookie"] = session_headers["Cookie"]
    if "Authorization" not in headers and session_headers.get("Authorization"):
        headers["Authorization"] = session_headers["Authorization"]

    method = spec["method"]
    kwargs: dict = {}
    if method == "GET":
        if spec["query"]:
            kwargs["params"] = spec["query"]
    else:
        post_data = spec["post_data"]
        content_type = spec["content_type"]
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
        return None, {"ok": False, "status": 0, "source_status": "network_error",
                      "message": f"候选来源网络异常：{exc}"}

    if response.status_code < 200 or response.status_code >= 300:
        source_status, message = _source_error(response.status_code, response.text[:200])
        return None, {"ok": False, "status": response.status_code,
                      "source_status": source_status, "message": message}
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return None, {"ok": False, "status": response.status_code,
                      "source_status": "invalid_response", "message": "候选来源返回的不是合法 JSON"}
    return data, {"ok": True, "status": response.status_code, "source_status": "ok", "message": ""}


def _enrich_select_sources(original):
    def wrapped(post_data: str | None, reads: list[dict], samples: dict | None = None) -> list[dict]:
        selects = original(post_data, reads, samples)
        by_url: dict[str, list[dict]] = {}
        for read in reads or []:
            by_url.setdefault(str(read.get("url") or ""), []).append(read)
        for sel in selects:
            matches = by_url.get(str(sel.get("source_url") or "")) or []
            if not matches:
                continue
            read = matches[-1]
            sel["source_method"] = str(read.get("method") or "GET").upper()
            if read.get("post_data") not in (None, ""):
                sel["source_post_data"] = read.get("post_data")
            if read.get("content_type"):
                sel["source_content_type"] = read.get("content_type")
            if read.get("auth_headers"):
                sel["source_auth_headers"] = read.get("auth_headers")
            records_path = _find_list_path(read.get("json"))
            if records_path is not None:
                sel["source_records_path"] = records_path
        return selects
    return wrapped


def _capture_read_request_metadata(original):
    async def wrapped(self, response) -> None:  # noqa: ANN001
        await original(self, response)
        try:
            from dano.execution.page import request_capture as rc

            request = response.request
            method = (request.method or "").upper()
            url = response.url
            headers = dict(request.headers or {})
            post_data = request.post_data if method in ("POST", "PUT", "PATCH") else None
            for read in reversed(self.reads):
                if read.get("method") != method or read.get("url") != url:
                    continue
                read["post_data"] = post_data
                read["content_type"] = headers.get("content-type", "")
                safe_headers = rc.extract_auth_headers(headers)
                if safe_headers:
                    read["auth_headers"] = safe_headers
                break
        except Exception:  # noqa: BLE001
            pass
    return wrapped


async def _fetch_options(sel: dict, *, base_url: str, storage_state, token_key: str | None,
                         verify: bool, auth_headers: dict | None) -> tuple[list, dict]:
    from dano.execution.page import request_capture as rc

    data, status = await _request_source_json(sel, base_url=base_url, storage_state=storage_state,
                                              token_key=token_key, verify=verify,
                                              auth_headers=auth_headers)
    if not status["ok"]:
        return [], status
    items = _extract_option_items(data, _source_spec(sel)["records_path"] or None)
    if items is None:
        return [], {"ok": False, "status": status["status"], "source_status": "invalid_shape",
                    "message": "候选来源响应结构已变化，无法定位候选列表"}
    items = rc._apply_option_filter(items, sel.get("option_filter"))
    return items, status


async def fetch_field_options(api_request: dict, field: str, *, base_url: str = "",
                              storage_state=None, token_key: str | None = None,
                              verify: bool = True, limit: int = 500) -> dict:
    from dano.execution.page import request_capture as rc

    sel = rc.find_field_select(api_request, field)
    mode = "value[]" if (sel or {}).get("kind") == "array" else "value"
    if not sel or not sel.get("source_url"):
        return {"field": field, "options": [], "count": 0, "submit_mode": mode,
                "source_status": "not_dynamic",
                "note": "该字段不是动态选择字段；请按字段说明传值"}
    items, status = await _fetch_options(sel, base_url=base_url, storage_state=storage_state,
                                         token_key=token_key, verify=verify,
                                         auth_headers=(api_request or {}).get("auth_headers"))
    if not status["ok"]:
        return {"field": field, "options": [], "count": 0, "submit_mode": mode,
                "source_status": status["source_status"], "note": status["message"],
                "http_status": status["status"]}
    lk, vk = sel.get("label_key"), sel.get("value_key")
    options = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get(lk, "")).strip()
        if label:
            options.append({"label": label, "value": rc._option_value(item.get(vk))})
        if len(options) >= limit:
            break
    source_status = "ok" if options else "empty"
    out = {"field": field, "options": options, "count": len(items), "submit_mode": mode,
           "label_key": lk, "value_key": vk, "source_status": source_status}
    if source_status == "empty":
        out["note"] = "当前条件下没有可选项"
    if sel.get("option_filter"):
        out["option_filter"] = sel.get("option_filter")
    return out


async def resolve_selects(api_request: dict, fields: dict, *, base_url: str, storage_state,
                          token_key: str | None, verify: bool) -> tuple[dict, dict]:
    """Resolve dynamic values fail-closed with precise source failure messages."""
    from dano.execution.page import request_capture as rc

    id_overrides: dict = {}
    for sel in api_request.get("selects") or []:
        param = sel.get("param")
        if param not in fields or not sel.get("source_url"):
            continue
        items, status = await _fetch_options(sel, base_url=base_url, storage_state=storage_state,
                                             token_key=token_key, verify=verify,
                                             auth_headers=api_request.get("auth_headers"))
        if not status["ok"]:
            raise ValueError(f"枚举字段 {param} 无法获取候选项：{status['message']}")
        submitted = fields[param]
        lk, vk = sel.get("label_key"), sel.get("value_key")
        if sel.get("kind") == "array":
            matches = []
            for value in rc._select_values(submitted):
                match, _ = rc._match_select_item(items, lk, vk, value)
                if match is None:
                    raise ValueError(f"枚举数组字段 {param} 的值 {value!r} 不在当前候选项中")
                matches.append(match)
            tokens = sel.get("array_tokens") or rc._split_path(sel.get("array_path") or sel.get("path", ""))
            rebuilt = rc._build_array_select_items(sel, matches)
            id_overrides[tuple(tokens)] = rebuilt
            for derived in sel.get("derived_count_paths") or []:
                dtokens = derived.get("tokens") or rc._split_path(derived.get("path", ""))
                id_overrides[tuple(dtokens)] = len(rebuilt)
            continue
        match, _ = rc._match_select_item(items, lk, vk, submitted)
        if match is None:
            raise ValueError(f"枚举字段 {param} 的值 {submitted!r} 不在当前候选项中")
        if sel.get("id_tokens") or sel.get("id_path"):
            if lk in match:
                fields[param] = match[lk]
            if vk in match:
                tokens = sel.get("id_tokens") or rc._split_path(sel.get("id_path", ""))
                id_overrides[tuple(tokens)] = match[vk]
        elif vk in match:
            fields[param] = match[vk]
    return fields, id_overrides


def install_option_p0() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from dano.execution.page import recorder as recorder_module
    from dano.execution.page import request_capture as rc

    rc.suggest_selects = _enrich_select_sources(rc.suggest_selects)
    rc.fetch_field_options = fetch_field_options
    rc._resolve_selects = resolve_selects
    if not getattr(recorder_module.RecordSession._on_response, "__dano_option_p0__", False):
        patched = _capture_read_request_metadata(recorder_module.RecordSession._on_response)
        patched.__dano_option_p0__ = True
        recorder_module.RecordSession._on_response = patched
    _INSTALLED = True
