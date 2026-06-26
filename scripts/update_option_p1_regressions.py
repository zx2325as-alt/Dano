from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "back/tests/test_request_capture.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''def test_manifest_enum_inlines_small_options():
    """问题1:候选 ≤50 → manifest schema 内置 enum(function-calling 层约束提交 value)。"""
''',
        '''def test_manifest_dynamic_enum_uses_private_query_contract():
    """动态候选不公开录制快照;调用方通过 Dano 的 option-query/v1 实时查询。"""
''',
        "small dynamic test name",
    )
    text = replace_once(
        text,
        '''    assert p["enum"] == ["1", "2", "3"]
    assert p["x-options"] == [{"label": "事假", "value": "1"},
                              {"label": "病假", "value": "2"},
                              {"label": "年假", "value": "3"}]
    assert p["x-submit-mode"] == "value"
''',
        '''    assert "enum" not in p and "x-options" not in p
    assert p["x-options-source"] is True
    assert p["x-options-protocol"] == "option-query/v1"
    assert p["x-options-search"] is True
    assert p["x-submit-mode"] == "value"
''',
        "small dynamic assertions",
    )
    text = replace_once(
        text,
        '''def test_manifest_large_options_no_inline_enum_but_snapshot():
    """问题1:候选 >50 → 不内置 enum(过大),但仍快照进 x-options(写 OPTIONS.md 供 agent 选)。"""
''',
        '''def test_manifest_large_dynamic_options_do_not_publish_snapshot():
    """动态候选无论大小都不公开录制快照,避免调用方使用过期数据。"""
''',
        "large dynamic test name",
    )
    text = replace_once(
        text,
        '''    assert "enum" not in p and len(p["x-options"]) == 135      # 不内置 enum,但快照全在
    assert p["x-options"][0] == {"label": "系统0", "value": "id0"}
    assert p.get("x-options-source") is True                   # 有来源接口 → 可 --list-options 实时拉
    assert "--list-options" in p["description"]
''',
        '''    assert "enum" not in p and "x-options" not in p
    assert p["x-options-source"] is True
    assert p["x-options-protocol"] == "option-query/v1"
    assert p["x-options-page-size"] == 50
''',
        "large dynamic assertions",
    )
    text = replace_once(
        text,
        '''    assert m.skill_interface["input_schema"]["properties"]["审批人"]["x-source-id"]
    assert next(iter(m.source_schema.values()))["url"] == "/users"
    assert m.skill_interface["bindings"][0]["target_path"] == "approverId"
''',
        '''    assert m.skill_interface["input_schema"]["properties"]["审批人"]["x-source-id"]
    source = next(iter(m.source_schema.values()))
    assert source["kind"] == "dynamic_options"
    assert source["protocol"] == "option-query/v1"
    assert "url" not in source
    assert m.skill_interface["bindings"][0]["mode"] == "select_value"
    assert "target_path" not in m.skill_interface["bindings"][0]
''',
        "legacy public interface assertions",
    )
    PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
