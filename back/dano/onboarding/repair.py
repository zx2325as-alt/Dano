"""LLM-assisted deterministic repair loop.

The model proposes only restricted operations. Legacy assets still use the historical
``api_request`` executor. P5 assets apply every operation to Transaction IR, recompile the
artifact and run deterministic self-check; direct executable mutation is not accepted.
"""
from __future__ import annotations

import json
import re

import structlog

log = structlog.get_logger(__name__)

_DRY_MODE_RE = re.compile(
    r"dry\s*=?\s*true|self[_\s]?check|未真[实]?.{0,3}跑|真实跑通|未.{0,2}执行|未真发|仅构造未真发|construct.*not.*sen",
    re.I,
)


def is_dry_mode_reason(reason) -> bool:
    return bool(_DRY_MODE_RE.search(str(reason or "")))


_FIX_SYSTEM = (
    "你是录制型 API Skill 的修复器。给定业务目标 goal、Transaction IR 骨架和 findings，"
    "只输出受限修复操作；引用必须来自骨架。P5 下操作修改 IR，程序重新编译 api_request，绝不能直接编辑请求制品。\n"
    '- {"op":"remap_field","param":"X","target_path":"a.b","step":0}\n'
    '- {"op":"rename_param","old":"X","new":"Y"}\n'
    '- {"op":"parameterize","path":"a.b","param":"X","step":0}\n'
    '- {"op":"link_step","target_step":1,"target_path":"taskId","source_step":0,"source_path":"data.id"}\n'
    '- {"op":"drop_step","step":0}\n'
    '- {"op":"reorder_steps","order":[0,1]}\n'
    '- {"op":"set_success_rule","field":"code","ok_values":["0"]}\n'
    '- {"op":"set_fact_check","endpoint":"/records","match_field":"reason","param":"原因"}\n'
    '- {"op":"set_identity","path":"applicantId","source":"localStorage:user.id","step":0}\n'
    '- {"op":"set_source_binding","param":"审批人","source_id":"src_x","mode":"select_value"}\n'
    '- {"op":"set_option_query","source_id":"src_x","protocol":{...}}\n'
    "session_constant → link_step/parameterize/drop_step；placeholder_name → rename_param；"
    "字段错配 → remap_field；越权身份 → set_identity；候选源错误 → set_source_binding。"
    "拿不准就输出空数组，不得编造路径、来源或业务名。输出 JSON：{\"ops\":[...]}。"
)


def _request_skeleton(api_request: dict) -> dict:
    """Expose metadata and IR topology only; never expose credentials or request values."""
    from dano.execution.page.request_capture import _leaf_paths

    def tx_ir(ir: dict) -> dict:
        if not isinstance(ir, dict):
            return {}
        execution = ir.get("execution") or {}
        return {
            "version": ir.get("version"),
            "compile": ir.get("compile") or {},
            "inputs": [
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "tokens": item.get("tokens"),
                    "step": item.get("step"),
                    "type": item.get("type"),
                    "submit_mode": item.get("submit_mode"),
                    "source_id": item.get("source_id"),
                    "required": item.get("required"),
                    "evidence": item.get("evidence"),
                }
                for item in (ir.get("inputs") or [])
            ],
            "sources": [
                {
                    "id": source.get("id"),
                    "kind": source.get("kind"),
                    "method": source.get("method"),
                    "has_url": bool(source.get("url")),
                    "value_key": source.get("value_key"),
                    "label_key": source.get("label_key"),
                    "records_path": source.get("records_path"),
                    "query_protocol": source.get("query_protocol") or {},
                    "count": source.get("count"),
                    "evidence": source.get("evidence"),
                }
                for source in (ir.get("sources") or [])
            ],
            "bindings": [
                {
                    "input": binding.get("input"),
                    "target_path": binding.get("target_path"),
                    "target_tokens": binding.get("target_tokens"),
                    "step": binding.get("step"),
                    "mode": binding.get("mode"),
                    "source_id": binding.get("source_id"),
                    "target_key": binding.get("target_key"),
                    "expand_fields": binding.get("expand_fields"),
                }
                for binding in (ir.get("bindings") or [])
            ],
            "identity": [
                {
                    "path": item.get("path"),
                    "tokens": item.get("tokens"),
                    "step": item.get("step"),
                    "source_kind": str(item.get("source") or "").partition(":")[0],
                }
                for item in (ir.get("identity") or [])
            ],
            "derived": [
                {
                    "kind": item.get("kind"),
                    "source_path": item.get("source_path"),
                    "target_path": item.get("target_path"),
                    "param": item.get("param"),
                    "step": item.get("step"),
                    "style": item.get("style"),
                }
                for item in (ir.get("derived") or [])
            ],
            "workflow": {
                "kind": execution.get("kind"),
                "request_count": len(execution.get("requests") or []),
                "links": execution.get("links") or [],
            },
            "success": ir.get("success") or {},
            "fact_check": bool(ir.get("fact_check")),
            "capture": {
                "capture_hash": (ir.get("capture") or {}).get("capture_hash"),
                "trace_hash": (ir.get("capture") or {}).get("trace_hash"),
                "write_event": (ir.get("capture") or {}).get("write_event"),
            },
        }

    def one(req: dict) -> dict:
        template = req.get("body_template")
        placements = []
        if isinstance(template, (dict, list)):
            for _path, tokens, shown, _raw in _leaf_paths(template):
                if isinstance(shown, str) and shown.startswith("{{") and shown.endswith("}}"):
                    placements.append({"path": tokens, "param": shown[2:-2]})
        return {
            "method": req.get("method"),
            "path": req.get("path"),
            "params": req.get("params"),
            "placements": placements,
            "identity": [item.get("path") for item in (req.get("identity") or [])],
        }

    steps = api_request.get("steps")
    skeleton = {"steps": [one(step) for step in steps]} if steps else one(api_request)
    skeleton["transaction_ir"] = tx_ir(api_request.get("transaction_ir") or {})
    return skeleton


def review_findings(verdicts) -> list[dict]:
    out: list[dict] = []
    for verdict in verdicts or []:
        passed = verdict.get("passed") if isinstance(verdict, dict) else getattr(verdict, "passed", True)
        role = verdict.get("role") if isinstance(verdict, dict) else getattr(verdict, "role", "")
        reasons = (verdict.get("reasons") if isinstance(verdict, dict) else getattr(verdict, "reasons", [])) or ["未通过"]
        if not passed:
            out += [{"kind": f"review_{role}", "detail": reason} for reason in reasons]
    return out


async def generate_fix_ops(client, model, *, goal, api_request, findings) -> list[dict]:
    if client is None or not model or not findings:
        return []
    payload = json.dumps(
        {"goal": goal or {}, "skeleton": _request_skeleton(api_request), "findings": findings},
        ensure_ascii=False,
    )
    try:
        out = await client.complete_json(
            model=model,
            system=_FIX_SYSTEM,
            user="【修复输入】\n" + payload,
            timeout_s=45.0,
        )
    except Exception:  # noqa: BLE001
        log.warning("generate_fix_ops.failed")
        return []
    ops = out.get("ops") if isinstance(out, dict) else None
    return [item for item in ops if isinstance(item, dict) and item.get("op")] if isinstance(ops, list) else []


async def run_repair_loop(api_request, propose, *, goal=None, seed_findings=None, max_rounds: int = 3):
    """Return a freshly compiled artifact plus repair history and remaining findings."""
    from dano.execution.page.ir_compiler import is_ir_authoritative
    from dano.execution.page.repair_ops import apply_fix_ops, collect_repair_findings

    use_ir = is_ir_authoritative(api_request)
    if use_ir:
        from dano.execution.page.ir_repair_p5 import apply_ir_fix_ops

    artifact = api_request
    history: list[dict] = []
    previous_count = None
    for round_index in range(max_rounds):
        findings = collect_repair_findings(artifact)
        if round_index == 0 and seed_findings:
            findings = findings + list(seed_findings)
        if not findings:
            log.info("repair.converged", round=round_index, source="transaction_ir" if use_ir else "api_request")
            return artifact, round_index, history, []
        if previous_count is not None and len(findings) >= previous_count:
            log.warning("repair.stalled", round=round_index, findings=len(findings))
            return artifact, round_index, history, findings
        previous_count = len(findings)
        ops = await propose(artifact, findings, goal) or []
        if not ops:
            log.warning("repair.no_ops", round=round_index, findings=len(findings))
            return artifact, round_index, history, findings
        if use_ir:
            artifact, applied, rejected = apply_ir_fix_ops(artifact, ops)
        else:
            artifact, applied, rejected = apply_fix_ops(artifact, ops)
        log.info(
            "repair.round",
            round=round_index,
            findings=len(findings),
            applied=len(applied),
            rejected=len(rejected),
            source="transaction_ir" if use_ir else "api_request",
        )
        history.append({
            "round": round_index,
            "findings": len(findings),
            "applied": applied,
            "rejected": rejected,
            "source": "transaction_ir" if use_ir else "api_request",
        })
    return artifact, max_rounds, history, collect_repair_findings(artifact)
