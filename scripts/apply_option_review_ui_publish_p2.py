from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1, got {count}")
    return text.replace(old, new, 1)


path = Path("skillfrontend/src/components/PageRecorder.tsx")
text = path.read_text(encoding="utf-8")
text = one(text,
    '    const selList = Object.values(selects).filter((s) => param_map[s.path]);   // 选领导:仅作为参数的\n'
    '    const idList = Object.values(identity);                                     // 当前用户:运行期重取\n',
    '    const option_query_decisions = Object.values(selects)\n'
    '      .map((s) => s.inference?.review_id)\n'
    '      .filter((reviewId): reviewId is string => !!reviewId)\n'
    '      .map((reviewId) => ({ review_id: reviewId, decision: reviewDecisions[reviewId] }));\n',
    "decision payload")
text = one(text,
    '    return { param_map, selList, idList, step_idxs };\n',
    '    return { param_map, option_query_decisions, step_idxs };\n',
    "payload return")
text = one(text,
    '    const { param_map, selList, idList, step_idxs } = _payload();\n'
    '    if (!Object.keys(param_map).length) { message.error("至少勾选一个字段作为参数"); return; }\n',
    '    const unresolved = Object.values(selects)\n'
    '      .map((s) => s.inference?.review_id)\n'
    '      .filter((reviewId): reviewId is string => !!reviewId && !reviewDecisions[reviewId]);\n'
    '    if (unresolved.length) { message.error(`还有 ${unresolved.length} 条查询能力需要确认`); return; }\n'
    '    const { param_map, option_query_decisions, step_idxs } = _payload();\n'
    '    if (!Object.keys(param_map).length) { message.error("至少勾选一个字段作为参数"); return; }\n',
    "unresolved guard")
text = one(text,
    '    send({ type: "publish_request", action: action.trim(), title: title.trim(),\n'
    '           param_map, selects: selList, identity: idList, step_idxs, transaction_ir: transactionIr });\n',
    '    send({ type: "publish_request", action: action.trim(), title: title.trim(),\n'
    '           param_map, option_query_decisions, step_idxs });\n',
    "publish payload")
text = one(text,
    '                          <Tag color="purple" style={{ fontSize: 11 }}>\n'
    '                            📋 选自列表 {sel.label_key}→{sel.value_key}(共{sel.count}项)\n'
    '                          </Tag>\n'
    '                          <OptionInferenceSummary select={sel} />\n',
    '                          <Tag color="purple" style={{ fontSize: 11 }}>\n'
    '                            📋 选自列表（共{sel.count}项）\n'
    '                          </Tag>\n'
    '                          <OptionInferenceSummary\n'
    '                            select={sel}\n'
    '                            decision={sel.inference?.review_id ? reviewDecisions[sel.inference.review_id] : undefined}\n'
    '                            onDecision={sel.inference?.review_id\n'
    '                              ? (decision) => setReviewDecisions((current) => ({\n'
    '                                  ...current, [sel.inference!.review_id!]: decision,\n'
    '                                }))\n'
    '                              : undefined}\n'
    '                          />\n',
    "review controls")
path.write_text(text, encoding="utf-8")
