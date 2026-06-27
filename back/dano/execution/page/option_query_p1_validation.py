"""Additional deterministic validation for the P1 option-query protocol.

The first P1 layer focuses on request construction. This layer closes the remaining
publication/runtime gaps without introducing free-form expressions:

* dependency context is bounded by both field count and encoded size;
* cursor pagination must expose an explicit response cursor path;
* write-time validation can use a typed exact-value binding instead of misusing a
  human-name search field;
* non-paginated sources never synthesize pagination metadata.
"""
from __future__ import annotations

import copy
from contextvars import ContextVar
import json
from typing import Any

_INSTALLED = False
_MAX_CONTEXT_BYTES = 64 * 1024
_MISSING = object()
_SUBMITTED_VALUE: ContextVar[Any] = ContextVar(
    "dano_option_query_submitted_value",
    default=_MISSING,
)


def install_option_query_p1_validation() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.execution.page import option_query_p1 as p1

    # The original P1 helper converts search text to str. Preserve the raw submitted
    # value in the current async context so an exact validation binding keeps integer,
    # boolean and structured value types intact.
    original_submitted_query = p1._submitted_query

    def submitted_query_preserving_type(value: Any) -> str:
        _SUBMITTED_VALUE.set(value)
        return original_submitted_query(value)

    original_prepare = p1._prepare_select

    def prepare_select_validated(
        select: dict,
        *,
        query: Any = None,
        cursor: Any = None,
        limit: int | None = None,
        context: dict | None = None,
        validation: bool = False,
    ) -> dict:
        normalized_context = dict(context or {})
        try:
            encoded_size = len(
                json.dumps(
                    normalized_context,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise p1.OptionQueryError(
                "invalid_context",
                "候选依赖上下文必须可以安全序列化",
            ) from exc
        if encoded_size > _MAX_CONTEXT_BYTES:
            raise p1.OptionQueryError(
                "invalid_context",
                f"候选依赖上下文超过 {_MAX_CONTEXT_BYTES} 字节安全上限",
            )

        protocol = p1._protocol(select)
        pagination = protocol.get("pagination")
        if pagination is not None:
            if not isinstance(pagination, dict):
                raise p1.OptionQueryError("invalid_query_protocol", "pagination 必须是对象")
            mode = str(pagination.get("mode") or "page").lower()
            response = protocol.get("response") or {}
            if mode == "cursor" and (
                not isinstance(response, dict) or not response.get("next_cursor_path")
            ):
                raise p1.OptionQueryError(
                    "invalid_query_protocol",
                    "cursor 分页必须声明 response.next_cursor_path",
                )

        validation_spec = protocol.get("validation") if validation else None
        if validation_spec is None:
            return original_prepare(
                select,
                query=query,
                cursor=cursor,
                limit=limit,
                context=normalized_context,
                validation=validation,
            )
        if not isinstance(validation_spec, dict):
            raise p1.OptionQueryError("invalid_query_protocol", "validation 必须是对象")

        raw_submitted = _SUBMITTED_VALUE.get()
        _SUBMITTED_VALUE.set(_MISSING)
        if raw_submitted is _MISSING:
            raw_submitted = query
        exact_value = p1._unwrap_value(raw_submitted)
        if exact_value in (None, "", []):
            raise p1.OptionQueryError(
                "validation_unsupported",
                "候选值缺少可用于精确验证的提交值",
            )

        # Exact validation and display search are separate bindings. Remove the search
        # binding for this one request, retain dependencies/pagination, then inject the
        # submitted value through the typed validation path.
        prepared_source = copy.deepcopy(select)
        prepared_protocol = copy.deepcopy(protocol)
        prepared_protocol.pop("search", None)
        prepared_source["option_query"] = prepared_protocol
        prepared = original_prepare(
            prepared_source,
            query=None,
            cursor=cursor,
            limit=limit,
            context=normalized_context,
            validation=True,
        )
        p1._inject(prepared, validation_spec, exact_value)
        prepared.setdefault("_option_query_runtime", {})["validation_strategy"] = "exact"
        return prepared

    original_page_info = p1._response_page_info

    def response_page_info_validated(select: dict, data: Any) -> dict:
        protocol = p1._protocol(select)
        if protocol.get("pagination") is None:
            return {}
        return original_page_info(select, data)

    p1._submitted_query = submitted_query_preserving_type
    p1._prepare_select = prepare_select_validated
    p1._response_page_info = response_page_info_validated
    _INSTALLED = True
