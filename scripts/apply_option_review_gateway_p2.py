from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1, got {count}")
    return text.replace(old, new, 1)


path = Path("back/dano/gateway/app.py")
text = path.read_text(encoding="utf-8")
text = one(text,
    "    from dano.execution.page.dataflow import build_transaction_ir, infer_request_transaction\n",
    "    from dano.execution.page.dataflow import build_transaction_ir, infer_request_transaction\n"
    "    from dano.execution.page.option_query_review_p2 import (\n"
    "        prepare_reviewable_selects, public_selects, public_transaction_ir, trusted_identity,\n"
    "    )\n",
    "imports")
text = one(text,
    '    fields = tx["fields"]\n    selects = tx["selects"]\n    identity = tx["identity"]\n',
    '    fields = tx["fields"]\n'
    '    server_selects = prepare_reviewable_selects(tx["selects"])\n'
    '    server_identity = trusted_identity(tx["identity"])\n',
    "capture state")
text = one(text,
    '                                                selects=selects, identity=identity, samples=samples,\n',
    '                                                selects=server_selects, identity=server_identity, samples=samples,\n',
    "ir inputs")
text = one(text,
    '                                                trace_ir=trace_ir)\n    return {"type": "request_fields",\n',
    '                                                trace_ir=trace_ir)\n'
    '    server_ir = tx["transaction_ir"]\n'
    '    return {"type": "request_fields",\n',
    "server ir")
text = one(text,
    '            "method": (chosen.get("method") or "POST").upper(), "url": chosen.get("url"),\n',
    '            "method": (chosen.get("method") or "POST").upper(), "url": _path(chosen.get("url") or ""),\n',
    "public url")
text = one(text,
    '            "selects": selects,\n'
    '            "identity": identity,\n',
    '            "selects": public_selects(server_selects),\n'
    '            "identity": [{"path": item.get("path")} for item in server_identity if item.get("path")],\n',
    "public projections")
text = one(text,
    '            "transaction_ir": tx["transaction_ir"]}   # 字段=当前用户/会话值(运行期重取;排除用户填值/平凡撞值)\n',
    '            "transaction_ir": public_transaction_ir(server_ir),\n'
    '            "_server_selects": server_selects,\n'
    '            "_server_identity": server_identity,\n'
    '            "_server_transaction_ir": server_ir}\n',
    "internal payload")
text = one(text,
    '        pending_ir: dict | None = None         # 事务级 IR: inputs/sources/bindings/constants/success 的权威捕获模型\n'
    '        pending_trace: dict | None = None      # Trace IR:录制事实时间线(仅 hash/事件引用进前端协议)\n',
    '        pending_ir: dict | None = None         # 事务级 IR: inputs/sources/bindings/constants/success 的权威捕获模型\n'
    '        pending_selects: list[dict] = []       # 服务端权威 select/query 元数据\n'
    '        pending_identity: list[dict] = []      # 服务端权威 identity 绑定\n'
    '        pending_trace: dict | None = None      # Trace IR:录制事实时间线(仅 hash/事件引用进前端协议)\n',
    "pending state")
old = '                    pending_ir = rf.get("transaction_ir")\n                    await ws.send_json(rf)\n'
new = ('                    pending_ir = rf.pop("_server_transaction_ir", None)\n'
       '                    pending_selects = rf.pop("_server_selects", [])\n'
       '                    pending_identity = rf.pop("_server_identity", [])\n'
       '                    await ws.send_json(rf)\n')
if text.count(old) != 2:
    raise SystemExit(f"capture response: expected 2, got {text.count(old)}")
text = text.replace(old, new, 2)
text = one(text,
    '                sels = msg.get("selects") or []         # Q2 选领导:展示 label、提交 value\n'
    '                idens = msg.get("identity") or []        # Q1 当前用户:运行期重取\n'
    '                tx_ir = _trusted_transaction_ir(pending_ir, msg.get("transaction_ir"), pending_trace)\n',
    '                from dano.execution.page.option_query_review_p2 import (\n'
    '                    apply_option_review_decisions, synchronize_transaction_ir, trusted_identity,\n'
    '                )\n'
    '                try:\n'
    '                    sels = apply_option_review_decisions(pending_selects, msg.get("option_query_decisions"))\n'
    '                except ValueError as exc:\n'
    '                    await ws.send_json({"type": "result", "report": {"ok": False, "reason": str(exc)}})\n'
    '                    continue\n'
    '                idens = trusted_identity(pending_identity)\n'
    '                reviewed_ir = synchronize_transaction_ir(pending_ir, sels)\n'
    '                tx_ir = _trusted_transaction_ir(reviewed_ir, None, pending_trace)\n'
    '                if tx_ir is None:\n'
    '                    await ws.send_json({"type": "result", "report": {"ok": False, "reason": "服务端事务模型校验失败，请重新录制"}})\n'
    '                    continue\n',
    "publish trust")
path.write_text(text, encoding="utf-8")
