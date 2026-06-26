"""Stable capability projection for the P1 option query protocol.

Clients need to know that remote search, dependencies, pagination and exact validation
exist even when the first request is intentionally blocked by ``query_required`` or
``missing_dependency``. This wrapper adds the same capability metadata to success and
failure responses and rejects cursor protocols that cannot produce a next cursor.
"""
from __future__ import annotations

_INSTALLED = False


def _capabilities(select: dict) -> tuple[dict, dict | None]:
    protocol = select.get("option_query") or {}
    if not isinstance(protocol, dict):
        return {}, {
            "source_status": "invalid_query_protocol",
            "note": "候选查询协议必须是对象",
        }

    search = protocol.get("search")
    pagination = protocol.get("pagination")
    response = protocol.get("response") or {}
    dependencies = protocol.get("dependencies") or []
    validation = protocol.get("validation")
    if search is not None and not isinstance(search, dict):
        return {}, {"source_status": "invalid_query_protocol", "note": "search 必须是对象"}
    if pagination is not None and not isinstance(pagination, dict):
        return {}, {"source_status": "invalid_query_protocol", "note": "pagination 必须是对象"}
    if validation is not None and not isinstance(validation, dict):
        return {}, {"source_status": "invalid_query_protocol", "note": "validation 必须是对象"}
    if not isinstance(response, dict):
        return {}, {"source_status": "invalid_query_protocol", "note": "response 必须是对象"}
    if not isinstance(dependencies, list):
        return {}, {"source_status": "invalid_query_protocol", "note": "dependencies 必须是数组"}

    mode = str((pagination or {}).get("mode") or "page").lower() if pagination else None
    if mode == "cursor" and not response.get("next_cursor_path"):
        return {}, {
            "source_status": "invalid_query_protocol",
            "note": "cursor 分页必须声明 response.next_cursor_path",
        }

    depends_on = [
        str(item.get("field"))
        for item in dependencies
        if isinstance(item, dict) and item.get("field")
    ]
    caps = {
        "search_supported": bool(search),
        "validation_supported": bool(validation),
        "depends_on": list(dict.fromkeys(depends_on)),
        "pagination_mode": mode,
    }
    if search:
        caps["min_query_length"] = max(0, int(search.get("min_length") or 0))
    return caps, None


def install_option_query_p1_contract() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.execution.page import request_capture as rc

    original = rc.fetch_field_options

    async def fetch_field_options_with_capabilities(api_request: dict, field: str, **kwargs) -> dict:
        select = rc.find_field_select(api_request, field)
        if not select or not select.get("option_query"):
            return await original(api_request, field, **kwargs)
        capabilities, error = _capabilities(select)
        if error is not None:
            mode = "value[]" if select.get("kind") == "array" else "value"
            return {
                "field": field,
                "options": [],
                "count": 0,
                "submit_mode": mode,
                **error,
                **capabilities,
            }
        result = await original(api_request, field, **kwargs)
        return {**result, **capabilities}

    rc.fetch_field_options = fetch_field_options_with_capabilities
    _INSTALLED = True
