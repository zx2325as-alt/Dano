"""Safe manifest projection for P1 option-query capabilities.

Only business-facing capability flags are exposed. Source URLs, request bodies, headers,
response paths and other execution internals remain backend-only.
"""
from __future__ import annotations

_INSTALLED = False


def option_query_schema(select: dict | None) -> dict:
    protocol = (select or {}).get("option_query")
    if not isinstance(protocol, dict) or not protocol:
        return {}

    search = protocol.get("search") if isinstance(protocol.get("search"), dict) else None
    pagination = protocol.get("pagination") if isinstance(protocol.get("pagination"), dict) else None
    dependencies = (
        protocol.get("dependencies")
        if isinstance(protocol.get("dependencies"), list)
        else []
    )
    result = {
        "x-options-search": bool(search),
        "x-options-depends-on": list(dict.fromkeys(
            str(item.get("field"))
            for item in dependencies
            if isinstance(item, dict) and item.get("field")
        )),
        "x-options-validation": isinstance(protocol.get("validation"), dict),
    }
    if search:
        try:
            result["x-options-min-query-length"] = max(
                0, int(search.get("min_length") or 0)
            )
        except (TypeError, ValueError):
            result["x-options-min-query-length"] = 0
    if pagination:
        mode = str(pagination.get("mode") or "page").lower()
        if mode in {"page", "offset", "cursor"}:
            result["x-options-pagination"] = mode
    return result


def install_option_query_manifest_p1() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.catalog import manifest

    original = manifest._schema_prop

    def schema_prop_with_query_capabilities(
        skill,
        field: str,
        desc: str,
        select: dict | None = None,
    ) -> dict:
        prop = original(skill, field, desc, select)
        if isinstance(prop, dict) and select:
            prop.update(option_query_schema(select))
        return prop

    manifest._schema_prop = schema_prop_with_query_capabilities
    _INSTALLED = True
