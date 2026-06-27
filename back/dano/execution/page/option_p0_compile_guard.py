"""Compile-time privacy guard for option-source metadata.

Runtime blocking is not enough: recorded source metadata is persisted in Skill assets and
may be shown in diagnostics. This guard removes credentials from request bodies and URLs
before candidates reach the UI or compiler, while retaining explicit markers so runtime
validation fails closed instead of replaying an incomplete authentication flow.
"""
from __future__ import annotations

import copy
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_INSTALLED = False
_REDACTED = "__DANO_REDACTED__"
_EXTRA_SENSITIVE_KEYS = ("api_key", "apikey", "access_key", "accesskey")


def _sanitize_source_url(url: object) -> tuple[str, dict]:
    from dano.execution.page.option_p0_quality import _is_sensitive_key

    raw = str(url or "")
    if not raw:
        return raw, {}
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return raw, {"source_url_invalid": True}

    markers: dict = {}
    netloc = parsed.netloc
    if parsed.username is not None or parsed.password is not None:
        markers["source_url_had_credentials"] = True
        if hostname:
            host = f"[{hostname}]" if ":" in hostname else hostname
            netloc = f"{host}:{port}" if port else host
        else:
            netloc = ""

    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    sensitive_query_keys = [key for key, _ in query_pairs if _is_sensitive_key(key)]
    if sensitive_query_keys:
        markers["source_sensitive_query_keys"] = list(dict.fromkeys(sensitive_query_keys))
        query_pairs = [
            (key, _REDACTED if _is_sensitive_key(key) else value)
            for key, value in query_pairs
        ]

    sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(query_pairs), ""))
    return sanitized, markers


def sanitize_source_select(select: dict) -> dict:
    from dano.execution.page.option_p0_quality import _sanitize_select

    out = _sanitize_select(copy.deepcopy(select))
    source_url, markers = _sanitize_source_url(out.get("source_url"))
    out["source_url"] = source_url
    out.update(markers)
    return out


def _sanitize_compiled(compiled: dict | None) -> dict | None:
    if not isinstance(compiled, dict):
        return compiled
    if isinstance(compiled.get("selects"), list):
        compiled["selects"] = [
            sanitize_source_select(item) if isinstance(item, dict) else item
            for item in compiled["selects"]
        ]
    for step in compiled.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("selects"), list):
            step["selects"] = [
                sanitize_source_select(item) if isinstance(item, dict) else item
                for item in step["selects"]
            ]
    return compiled


def install_option_p0_compile_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.execution.page import option_p0_quality
    from dano.execution.page import request_capture as rc

    # Extend the common key classifier once so URL, form and JSON body handling agree.
    for key in _EXTRA_SENSITIVE_KEYS:
        if key not in option_p0_quality._SENSITIVE_KEY_PARTS:
            option_p0_quality._SENSITIVE_KEY_PARTS += (key,)

    original_suggest_selects = rc.suggest_selects

    def suggest_selects_guarded(post_data: str | None, reads: list[dict], samples: dict | None = None):
        return [
            sanitize_source_select(item)
            for item in (original_suggest_selects(post_data, reads, samples) or [])
        ]

    original_build_api_request = rc.build_api_request

    def build_api_request_guarded(
        request: dict,
        param_map: dict,
        base_url: str = "",
        selects: list[dict] | None = None,
        identity: list[dict] | None = None,
        typed: dict | None = None,
    ):
        safe_selects = [
            sanitize_source_select(item) if isinstance(item, dict) else item
            for item in (selects or [])
        ]
        compiled = original_build_api_request(
            request,
            param_map,
            base_url,
            safe_selects,
            identity,
            typed,
        )
        return _sanitize_compiled(compiled)

    rc.suggest_selects = suggest_selects_guarded
    rc.build_api_request = build_api_request_guarded
    _INSTALLED = True
