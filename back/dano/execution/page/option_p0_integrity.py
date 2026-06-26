"""Candidate integrity checks shared by option display and write execution.

The UI endpoint is not the only caller: agents may invoke a Skill directly with JSON.
Therefore ambiguity must be rejected inside the common candidate-fetch path, before
both ``fetch_field_options`` and ``_resolve_selects`` consume the records.
"""
from __future__ import annotations

import json

_INSTALLED = False


def _stable(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return repr(value)


def _error(status: dict, code: str, message: str, **extra) -> tuple[list, dict]:
    return [], {
        **status,
        "ok": False,
        "source_status": code,
        "message": message,
        **extra,
    }


def validate_candidate_items(select: dict, items: list, status: dict) -> tuple[list, dict]:
    """Reject ambiguous mappings before either display or execution uses them.

    Exact duplicate records are harmless and remain in the raw list so the public
    response normalizer can report how many were removed. Records with the same
    label/value but different extra fields are not interchangeable because those extra
    fields may be expanded into the final write request.
    """
    if not items:
        return items, status

    primitive = all(not isinstance(item, (dict, list)) for item in items)
    if primitive:
        return items, status

    if not all(isinstance(item, dict) for item in items):
        return _error(
            status,
            "mixed_candidate_shape",
            "候选来源同时返回对象和非对象，无法建立稳定映射",
        )

    label_key = select.get("label_key")
    value_key = select.get("value_key")
    if not label_key or not value_key:
        return _error(
            status,
            "invalid_mapping",
            "对象候选缺少显示字段或提交字段配置",
        )

    valid: list[dict] = []
    invalid_count = 0
    pair_to_record: dict[tuple[str, str], str] = {}
    values_to_labels: dict[str, set[str]] = {}
    labels_to_values: dict[str, set[str]] = {}

    for item in items:
        label_raw = item.get(label_key)
        value = item.get(value_key)
        label = str(label_raw or "").strip()
        if not label or value is None or value == "":
            invalid_count += 1
            continue

        value_repr = _stable(value)
        record_repr = _stable(item)
        pair = (label, value_repr)
        previous_record = pair_to_record.get(pair)
        if previous_record is not None and previous_record != record_repr:
            return _error(
                status,
                "ambiguous_records",
                "候选来源中相同名称和提交值对应多条不同记录，无法安全展开附属字段",
            )
        pair_to_record[pair] = record_repr
        values_to_labels.setdefault(value_repr, set()).add(label)
        labels_to_values.setdefault(label, set()).add(value_repr)
        valid.append(item)

    if not valid:
        return _error(
            status,
            "invalid_mapping",
            "候选来源返回了数据，但显示字段或提交字段已失效",
            invalid_item_count=invalid_count,
        )

    value_conflicts = sum(1 for labels in values_to_labels.values() if len(labels) > 1)
    if value_conflicts:
        return _error(
            status,
            "ambiguous_values",
            "候选来源存在相同提交值对应多个名称，无法安全选择",
            conflict_count=value_conflicts,
        )

    label_conflicts = sum(1 for values in labels_to_values.values() if len(values) > 1)
    if label_conflicts:
        return _error(
            status,
            "ambiguous_labels",
            "候选来源存在相同名称对应多个提交值，直接按名称调用会产生歧义",
            conflict_count=label_conflicts,
        )

    return valid, {
        **status,
        "invalid_item_count": invalid_count,
    }


def install_option_p0_integrity() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.execution.page import option_p0

    original_fetch_options = option_p0._fetch_options

    async def fetch_options_with_integrity(select: dict, *args, **kwargs):
        items, status = await original_fetch_options(select, *args, **kwargs)
        if not status.get("ok"):
            return items, status
        return validate_candidate_items(select, items, status)

    option_p0._fetch_options = fetch_options_with_integrity
    _INSTALLED = True
