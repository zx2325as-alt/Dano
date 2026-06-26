from pathlib import Path

path = Path("skillfrontend/src/components/PageRecorder.tsx")
text = path.read_text(encoding="utf-8")

replacements = [
    (
        'import { useNavigate } from "react-router-dom";\n',
        'import { useNavigate } from "react-router-dom";\n'
        'import OptionInferenceSummary, { OptionQueryInferenceView, OptionQueryProtocolView } from "./OptionInferenceSummary";\n',
    ),
    (
        'interface RecSelect { path: string; source_url: string; value_key: string; label_key: string; label: string; count: number }\n',
        'interface RecSelect {\n'
        '  path: string; source_url: string; value_key: string; label_key: string; label: string; count: number;\n'
        '  option_query?: OptionQueryProtocolView; option_query_inference?: OptionQueryInferenceView;\n'
        '}\n',
    ),
    (
        '{Object.keys(selects).length > 0 && <span><Tag color="purple">📋 选自列表</Tag>\n'
        '                    l、提交 value,运行期回填目标系统值;</span>}\n',
        '{Object.keys(selects).length > 0 && <span><Tag color="purple">📋 选自列表</Tag>\n'
        '                    展示 label、提交 value，运行期回填目标系统值;</span>}\n',
    ),
    (
        '{sel && <Tag color="purple" style={{ fontSize: 11 }}>\n'
        '                          📋 选自列表 {sel.label_key}→{sel.value_key}(共{sel.count}项)</Tag>}\n',
        '{sel && <>\n'
        '                          <Tag color="purple" style={{ fontSize: 11 }}>\n'
        '                            📋 选自列表 {sel.label_key}→{sel.value_key}(共{sel.count}项)\n'
        '                          </Tag>\n'
        '                          <OptionInferenceSummary select={sel} />\n'
        '                        </>}\n',
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one match, found {count}: {old[:100]!r}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
