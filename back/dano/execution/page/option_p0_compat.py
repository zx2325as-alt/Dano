"""Compatibility bridge for option assets created before P0 source metadata.

New recordings carry method/body/records-path metadata and use the strict P0 runtime.
Legacy assets only contain ``source_url`` and historically relied on ``_fetch_list``;
keep that behavior so existing Skills and callers are not forced onto an unverifiable
request shape during the P0 rollout.
"""
from __future__ import annotations

_INSTALLED = False
_EXTENDED_SOURCE_KEYS = {
    "source_post_data",
    "source_content_type",
    "source_query",
    "source_headers",
    "source_records_path",
}


def _is_legacy_get_source(select: dict) -> bool:
    method = str(select.get("source_method") or "GET").upper()
    return method == "GET" and not any(key in select for key in _EXTENDED_SOURCE_KEYS)


def install_option_p0_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.execution.page import option_p0
    from dano.execution.page import request_capture as rc

    strict_fetch_options = option_p0._fetch_options

    async def fetch_options_compat(
        select: dict,
        *,
        base_url: str,
        storage_state,
        token_key: str | None,
        verify: bool,
        auth_headers: dict | None,
    ) -> tuple[list, dict]:
        if not _is_legacy_get_source(select):
            return await strict_fetch_options(
                select,
                base_url=base_url,
                storage_state=storage_state,
                token_key=token_key,
                verify=verify,
                auth_headers=auth_headers,
            )

        items = await rc._fetch_list(
            select.get("source_url") or "",
            base_url,
            storage_state,
            token_key,
            verify,
            auth_headers,
        )
        items = rc._apply_option_filter(items, select.get("option_filter"))
        return items, {
            "ok": True,
            "status": 200,
            "source_status": "legacy_get",
            "message": "",
        }

    strict_resolve_selects = rc._resolve_selects

    async def resolve_selects_compat(*args, **kwargs):
        try:
            return await strict_resolve_selects(*args, **kwargs)
        except ValueError as exc:
            # Keep the long-standing public error wording used by callers and tests.
            message = str(exc).replace("不在当前候选项中", "不在候选项中")
            raise ValueError(message) from exc

    option_p0._fetch_options = fetch_options_compat
    rc._resolve_selects = resolve_selects_compat
    _INSTALLED = True
