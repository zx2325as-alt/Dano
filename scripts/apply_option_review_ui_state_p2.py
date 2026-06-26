from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1, got {count}")
    return text.replace(old, new, 1)


path = Path("skillfrontend/src/components/PageRecorder.tsx")
text = path.read_text(encoding="utf-8")
text = one(text,
    'import OptionInferenceSummary, { OptionQueryInferenceView, OptionQueryProtocolView } from "./OptionInferenceSummary";\n',
    'import OptionInferenceSummary, { OptionCapabilitiesView, OptionQueryInferenceView, OptionReviewDecision } from "./OptionInferenceSummary";\n',
    "imports")
text = one(text,
    'interface RecSelect {\n'
    '  path: string; source_url: string; value_key: string; label_key: string; label: string; count: number;\n'
    '  option_query?: OptionQueryProtocolView; option_query_inference?: OptionQueryInferenceView;\n'
    '}\n',
    'interface RecSelect {\n'
    '  path: string; label: string; count: number; kind?: string;\n'
    '  capabilities?: OptionCapabilitiesView; inference?: OptionQueryInferenceView;\n'
    '}\n',
    "select type")
text = one(text,
    '  const [identity, setIdentity] = useState<Record<string, RecIdentity>>({});  // path → identity 建议\n'
    '  const [transactionIr, setTransactionIr] = useState<Record<string, any> | null>(null);\n',
    '  const [identity, setIdentity] = useState<Record<string, RecIdentity>>({});  // path → identity 建议\n'
    '  const [reviewDecisions, setReviewDecisions] = useState<Record<string, OptionReviewDecision>>({});\n',
    "review state")
text = text.replace('setIdentity({}); setTransactionIr(null); setStepSel({});', 'setIdentity({}); setReviewDecisions({}); setStepSel({});')
text = one(text,
    '        setSelects(selMap); setIdentity(idMap);\n'
    '        setTransactionIr(m.transaction_ir || null);\n'
    '        setFields(fs);\n',
    '        setSelects(selMap); setIdentity(idMap); setReviewDecisions({});\n'
    '        setFields(fs);\n',
    "request state")
path.write_text(text, encoding="utf-8")
