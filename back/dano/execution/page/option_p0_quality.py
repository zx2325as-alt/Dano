"""P0 data-quality and privacy rules for dynamic option sources.

This module keeps the compatibility rollout narrow while preventing three common
production failures:

* secrets in a recorded POST body being persisted into a Skill;
* a non-empty response being misreported as an empty enum because label/value keys
  drifted;
* duplicate or conflicting values making a selection ambiguous.
"""
from __future__ import annotations

import copy
import json
from urllib.parse import parse_qsl, urlencode

_INSTALLED = False
_MAX_SOURCE_ITEMS = 10_000
_MAX_RETURNED_OPTIONS = 500
_REDACTED = "__DANO_REDACTED__"
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "authorization",
    "cookie",
    "token",
    "secret",
    "credential",
    "session",
    "captcha",
    "verifycode",
    "client_secret",
    "clientsecret",
)


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key or "").lower().replace("-", "").replace("_", "")
    return any(part.replace("_", "") in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact_node(node, path: str = "") -> tuple[object, list[str]]:
    found: list[str] = []
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            current = f"{path}.{key}" if path else str(key)
            if _is_sensitive_key(key):
                out[key] = _REDACTED
                found.append(current)
            else:
                redacted, nested = _redact_node(value, current)
                out[key] = redacted
                found.extend(nested)
        return out, found
    if isinstance(node, list):
        out = []
        for index, value in enumerate(node):
            redacted, nested = _redact_node(value, f"{path}[{index}]")
            out.append(redacted)
            found.extend(nested)
        return out, found
    return node, found


def sanitize_source_post_data(post_data, content_type: str | None = None) -> tuple[object, list[str]]:
    """Return a shape-preserving redacted request body and sensitive key paths."""
    if post_data in (None, ""):
        return post_data, []
    if isinstance(post_data, (dict, list)):
        return _redact_node(copy.deepcopy(post_data))

    text = str(post_data)
    ctype = str(content_type or "").lower()
    if "form-urlencoded" in ctype or ("=" in text and not text.lstrip().startswith(("{", "["))):
        pairs = parse_qsl(text, keep_blank_values=True)
        if not pairs:
            return post_data, []
        found = [key for key, _ in pairs if _is_sensitive_key(key)]
        sanitized = [(key, _REDACTED if _is_sensitive_key(key) else value) for key, value in pairs]
        return urlencode(sanitized), found
    try:
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001
        return post_data, []
    redacted, found = _redact_node(parsed)
    return json.dumps(redacted, ensure_ascii=False, separators=(",", ":")), found


def sensitive_source_body_keys(select: dict) -> list[str]:
    explicit = list(select.get("source_sensitive_body_keys") or [])
    if explicit:
        return explicit
    _, found = sanitize_source_post_data(
        select.get("source_post_data"),
        select.get("source_content_type"),
    )
    return found


def _sanitize_select(select: dict) -> dict:
    out = copy.deepcopy(select)
    sanitized, found = sanitize_source_post_data(
        out.get("source_post_data"),
        out.get("source_content_type"),
    )
    if found:
        out["source_post_data"] = sanitized
        out["source_sensitive_body_keys"] = found
        out["source_body_redacted"] = True
    return out


def _stable_value(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return repr(value)


def _normalize_option_result(result: dict, requested_limit: int) -> dict:
    status = str(result.get("source_status") or "")
    if status not in {"ok", "empty"}:
        return result

    original = list(result.get("options") or [])
    unique: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    labels_by_value: dict[str, set[str]] = {}
    for option in original:
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or "").strip()
        value = option.get("value")
        if not label or value in (None, ""):
            continue
        value_key = _stable_value(value)
        pair = (label, value_key)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        labels_by_value.setdefault(value_key, set()).add(label)
        unique.append({"label": label, "value": value})

    conflicting = sorted(value for value, labels in labels_by_value.items() if len(labels) > 1)
    if conflicting:
        return {
            **result,
            "options": [],
            "source_status": "ambiguous_values",
            "note": "候选来源存在相同提交值对应多个名称，无法安全选择",
            "conflict_count": len(conflicting),
        }

    source_count = int(result.get("count") or 0)
    if source_count > 0 and not unique:
        return {
            **result,
            "options": [],
            "source_status": "invalid_mapping",
            "note": "候选来源返回了数据，但显示字段或提交字段已失效",
        }

    limit = max(1, min(int(requested_limit or _MAX_RETURNED_OPTIONS), _MAX_RETURNED_OPTIONS))
    returned = unique[:limit]
    normalized = {**result, "options": returned}
    normalized["source_status"] = "ok" if returned else "empty"
    normalized["deduplicated_count"] = max(0, len(original) - len(unique))
    normalized["truncated"] = len(unique) > len(returned)
    if normalized["truncated"]:
        normalized["note"] = f"候选项过多，仅返回前 {len(returned)} 项"
    return normalized


def install_option_p0_quality() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.execution.page import option_p0
    from dano.execution.page import request_capture as rc

    original_suggest_selects = rc.suggest_selects

    def suggest_selects_quality(post_data: str | None, reads: list[dict], samples: dict | None = None):
        return [_sanitize_select(item) for item in (original_suggest_selects(post_data, reads, samples) or [])]

    original_fetch_options = option_p0._fetch_options

    async def fetch_options_quality(*args, **kwargs):
        items, status = await original_fetch_options(*args, **kwargs)
        if status.get("ok") and len(items) > _MAX_SOURCE_ITEMS:
            return [], {
                "ok": False,
                "status": status.get("status", 200),
                "source_status": "too_many_options",
                "message": f"候选来源一次返回 {len(items)} 项，超过安全上限 {_MAX_SOURCE_ITEMS}",
            }
        return items, status

    original_fetch_field_options = rc.fetch_field_options

    async def fetch_field_options_quality(
        api_request: dict,
        field: str,
        *,
        base_url: str = "",
        storage_state=None,
        token_key: str | None = None,
        verify: bool = True,
        limit: int = _MAX_RETURNED_OPTIONS,
    ) -> dict:
        safe_limit = max(1, min(int(limit or _MAX_RETURNED_OPTIONS), _MAX_RETURNED_OPTIONS))
        result = await original_fetch_field_options(
            api_request,
            field,
            base_url=base_url,
            storage_state=storage_state,
            token_key=token_key,
            verify=verify,
            limit=safe_limit,
        )
        return _normalize_option_result(result, safe_limit)

    rc.suggest_selects = suggest_selects_quality
    option_p0._fetch_options = fetch_options_quality
    rc.fetch_field_options = fetch_field_options_quality
    _INSTALLED = True
