"""Public manifest projection for P3 opaque option references.

Recorded candidate snapshots and target-system IDs are private runtime evidence. New P3
assets expose only the broker contract: callers query Dano for current options and submit
the returned short-lived reference.
"""
from __future__ import annotations

_INSTALLED = False
REFERENCE_VERSION = "option-reference/v1"


def _requires_reference(select: dict | None) -> bool:
    return bool(isinstance(select, dict) and select.get("option_reference_required"))


def _scrub_raw_options(prop: dict) -> None:
    prop.pop("enum", None)
    prop.pop("x-options", None)
    prop.pop("x-options-truncated", None)
    items = prop.get("items")
    if isinstance(items, dict):
        items.pop("enum", None)
        items.pop("examples", None)


def install_option_reference_manifest_p3() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from dano.catalog import manifest

    original = manifest._schema_prop

    def schema_prop_with_option_reference(
        skill,
        field: str,
        desc: str,
        select: dict | None = None,
    ) -> dict:
        prop = original(skill, field, desc, select)
        if not isinstance(prop, dict) or not _requires_reference(select):
            return prop

        _scrub_raw_options(prop)
        is_array = (select or {}).get("kind") == "array"
        prop["type"] = "array" if is_array else "string"
        if is_array:
            prop["items"] = {"type": "string", "format": "option-reference"}
            prop["format"] = "option-reference-list"
            prop["x-submit-mode"] = "reference[]"
        else:
            prop["format"] = "option-reference"
            prop["x-submit-mode"] = "reference"
        prop["description"] = (
            desc
            + "(动态选择字段：先调用候选查询接口获取当前选项，再提交其短期候选引用；"
              "不要填写或猜测目标系统 ID。)"
        )
        prop["x-options-source"] = True
        prop["x-option-reference-required"] = True
        prop["x-option-reference-version"] = REFERENCE_VERSION
        prop["x-option-label"] = "label"
        prop["x-option-value"] = "reference"
        return prop

    manifest._schema_prop = schema_prop_with_option_reference
    _INSTALLED = True
