"""P2 write-validation fallback for searchable option sources.

Some systems expose a display-name search endpoint but submit a numeric/string stable ID.
Searching that endpoint with the submitted ID can produce a false negative. When the
recorded candidate snapshot contains one unambiguous ``value -> label`` mapping, use the
label only to perform the live search. The live response must still contain the submitted
stable value before the write may proceed, so the snapshot is never treated as authority.
"""
from __future__ import annotations

from typing import Any

_INSTALLED = False


def _submitted_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _unique_snapshot_label(select: dict, submitted: Any) -> str | None:
    raw = _submitted_value(submitted)
    matches: list[str] = []
    for option in select.get("options") or []:
        if not isinstance(option, dict):
            continue
        if str(option.get("value")) != str(raw):
            continue
        label = str(option.get("label") or "").strip()
        if label and label not in matches:
            matches.append(label)
    return matches[0] if len(matches) == 1 else None


def install_option_query_validation_p2() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.execution.page import option_query_p1 as p1

    original_prepare = p1._prepare_select

    def prepare_select_with_label_fallback(
        select: dict,
        *,
        query: Any = None,
        cursor: Any = None,
        limit: int | None = None,
        context: dict | None = None,
        validation: bool = False,
    ) -> dict:
        protocol = p1._protocol(select)
        search = protocol.get("search")
        exact = protocol.get("validation")
        effective_query = query
        strategy = None

        if validation and isinstance(search, dict) and exact is None:
            label = _unique_snapshot_label(select, query)
            if label and str(label) != str(query):
                effective_query = label
                strategy = "live_search_by_recorded_label"

        prepared = original_prepare(
            select,
            query=effective_query,
            cursor=cursor,
            limit=limit,
            context=context,
            validation=validation,
        )
        if strategy:
            runtime = prepared.setdefault("_option_query_runtime", {})
            runtime["validation_strategy"] = strategy
            runtime["submitted_value"] = _submitted_value(query)
            runtime["search_label"] = effective_query
        return prepared

    p1._prepare_select = prepare_select_with_label_fallback
    _INSTALLED = True
