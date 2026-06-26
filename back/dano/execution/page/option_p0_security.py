"""Security gate for P0 dynamic option-source replay.

Recorded option requests are untrusted asset data. Before the runtime sends one, this
module enforces the narrow read contract expected from an option source:

* only GET and POST are accepted;
* only HTTP(S) absolute URLs are accepted;
* credentials embedded in a URL are rejected;
* credentials embedded in a recorded request body are rejected;
* when a target-system base URL is known, the option source must be same-origin.

Relative URLs remain compatible when the legacy caller has not supplied ``base_url``;
they cannot redirect credentials to another origin by themselves and the underlying
runtime preserves its previous resolution/error behavior.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

_INSTALLED = False
_SAFE_OPTION_METHODS = {"GET", "POST"}


def _origin(url: str) -> tuple[str, str, int | None] | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme.lower(), parsed.hostname.lower(), port


def _validate_source_request(select: dict, base_url: str) -> dict | None:
    from dano.execution.page.option_p0_quality import sensitive_source_body_keys

    method = str(select.get("source_method") or "GET").upper()
    if method not in _SAFE_OPTION_METHODS:
        return {
            "ok": False,
            "status": 0,
            "source_status": "unsafe_method",
            "message": f"候选来源使用了不安全的方法 {method}；候选查询只允许 GET 或 POST",
        }

    sensitive_keys = sensitive_source_body_keys(select)
    if sensitive_keys:
        return {
            "ok": False,
            "status": 0,
            "source_status": "sensitive_request",
            "message": "候选来源请求体包含凭证或验证码字段，已禁止重放",
            "sensitive_keys": sensitive_keys,
        }

    raw_url = str(select.get("source_url") or "").strip()
    if not raw_url:
        return {
            "ok": False,
            "status": 0,
            "source_status": "invalid_source_url",
            "message": "候选来源 URL 为空",
        }

    parsed_raw = urlparse(raw_url)
    if parsed_raw.username is not None or parsed_raw.password is not None:
        return {
            "ok": False,
            "status": 0,
            "source_status": "credential_in_url",
            "message": "候选来源 URL 不允许包含用户名或密码",
        }

    if parsed_raw.scheme and parsed_raw.scheme.lower() not in {"http", "https"}:
        return {
            "ok": False,
            "status": 0,
            "source_status": "invalid_source_url",
            "message": "候选来源只允许 HTTP 或 HTTPS",
        }

    base = str(base_url or "").strip()
    if not parsed_raw.scheme and not base:
        # Legacy execute paths may resolve this later from their own request context.
        # A relative URL is not cross-origin by itself, so do not reject it here.
        return None

    full_url = raw_url if parsed_raw.scheme else urljoin(base.rstrip("/") + "/", raw_url.lstrip("/"))
    source_origin = _origin(full_url)
    if source_origin is None:
        return {
            "ok": False,
            "status": 0,
            "source_status": "invalid_source_url",
            "message": "候选来源 URL 无效",
        }

    if base:
        base_origin = _origin(base)
        if base_origin is None:
            return {
                "ok": False,
                "status": 0,
                "source_status": "invalid_base_url",
                "message": "目标系统 base_url 无效",
            }
        if source_origin != base_origin:
            return {
                "ok": False,
                "status": 0,
                "source_status": "cross_origin_blocked",
                "message": "候选来源与目标系统不是同源地址，已阻止发送登录凭证",
            }
    return None


def install_option_p0_security() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.execution.page import option_p0

    # Keep recorder/runtime method policy aligned. PUT/PATCH may be read-like in some
    # systems, but replaying them as a candidate lookup is too risky for P0.
    option_p0._ALLOWED_SOURCE_METHODS.clear()
    option_p0._ALLOWED_SOURCE_METHODS.update(_SAFE_OPTION_METHODS)

    original = option_p0._request_source_json

    async def guarded_request_source_json(
        select: dict,
        *,
        base_url: str,
        storage_state,
        token_key: str | None,
        verify: bool,
        auth_headers: dict | None,
    ):
        violation = _validate_source_request(select, base_url)
        if violation is not None:
            return None, violation
        return await original(
            select,
            base_url=base_url,
            storage_state=storage_state,
            token_key=token_key,
            verify=verify,
            auth_headers=auth_headers,
        )

    option_p0._request_source_json = guarded_request_source_json
    _INSTALLED = True
