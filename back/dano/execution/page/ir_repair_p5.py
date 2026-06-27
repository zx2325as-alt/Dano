"""Deterministic P5 repair executor.

For authoritative assets the LLM never edits ``api_request``. It proposes a restricted IR
patch, this module applies the patch to Transaction IR, validates the graph, recompiles the
artifact and runs the existing deterministic self-check. Invalid patches are rolled back
one operation at a time.
"""
from __future__ import annotations

import copy
from typing import Any

from dano.execution.page.ir_compiler import compile_transaction_ir, is_ir_authoritative
from dano.execution.page.ir_integrity_p5 import synchronize_constants
from dano.execution.page.request_capture import _PATH_MISSING, _infer_type, _leaf_paths, _path_lookup, self_check
from dano.execution.page.transaction_ir import validate_transaction_ir

_IR_FIX_OPS = {
    "drop_step",
    "reorder_steps",
    "set_success_rule",
    "set_fact_check",
    "parameterize",
    "link_step",
    "rename_param",
    "remap_field",
    "set_identity",
    "bind_placeholder",
    "set_source_binding",
    "set_option_query",
}


def _execution(ir: dict) -> dict:
    execution = ir.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("Transaction IR execution missing")
    return execution


def _requests(ir: dict) -> list[dict]:
    requests = _execution(ir).get("requests")
    if not isinstance(requests, list) or not requests:
        raise ValueError("Transaction IR requests missing")
    return requests


def _locate_path(ir: dict, path: str, step: int | None = None) -> tuple[int, list, Any] | None:
    matches: list[tuple[int, list, Any]] = []
    for index, request in enumerate(_requests(ir)):
        if isinstance(step, int) and index != step:
            continue
        for shown, tokens, _sv, raw in _leaf_paths(request.get("body")):
            if shown == path:
                matches.append((index, list(tokens), copy.deepcopy(raw)))
    return matches[0] if len(matches) == 1 else None


def _input(ir: dict, name: str) -> dict | None:
    return next((item for item in (ir.get("inputs") or []) if item.get("name") == name), None)


def _bindings(ir: dict, name: str) -> list[dict]:
    return [item for item in (ir.get("bindings") or []) if item.get("input") == name]


def _remove_unbound_input(ir: dict, name: str) -> None:
    if any(item.get("input") == name for item in (ir.get("bindings") or [])):
        return
    ir["inputs"] = [item for item in (ir.get("inputs") or []) if item.get("name") != name]


def _renumber_steps(ir: dict) -> None:
    requests = _requests(ir)
    ir["steps"] = [
        {
            "idx": index,
            "method": request.get("method") or "POST",
            "path": request.get("path") or request.get("url") or "/",
            "role": "selected_write" if index == len(requests) - 1 else "write",
        }
        for index, request in enumerate(requests)
    ]
    last = requests[-1]
    ir["method"] = last.get("method") or "POST"
    ir["url"] = last.get("url") or ""
    ir["path"] = last.get("path") or "/"


def _apply_one(ir: dict, op: dict) -> tuple[bool, str]:  # noqa: C901
    name = op.get("op")
    if name not in _IR_FIX_OPS:
        return False, f"未知 IR 操作 {name}"

    if name == "rename_param":
        old = str(op.get("old") or op.get("param") or "")
        new = str(op.get("new") or "")
        if not old or not new or _input(ir, new):
            return False, "缺 old/new 或新参数已存在"
        input_item = _input(ir, old)
        if input_item is None:
            return False, f"参数 {old} 不存在"
        input_item["name"] = new
        for binding in _bindings(ir, old):
            binding["input"] = new
        for derived in ir.get("derived") or []:
            if derived.get("param") == old:
                derived["param"] = new
        for source in ir.get("sources") or []:
            for dependency in (source.get("query_protocol") or {}).get("dependencies") or []:
                if dependency.get("field") == old:
                    dependency["field"] = new
        if (ir.get("fact_check") or {}).get("param") == old:
            ir["fact_check"]["param"] = new
        goal = ir.get("goal") or {}
        goal["required_inputs"] = [new if value == old else value for value in goal.get("required_inputs") or []]
        return True, "ok"

    if name in {"remap_field", "bind_placeholder"}:
        param = str(op.get("param") or "")
        target_path = str(op.get("target_path") or "") if not isinstance(op.get("target_path"), list) else ""
        binding = next(iter(_bindings(ir, param)), None)
        if binding is None:
            return False, f"参数 {param} 无绑定"
        if isinstance(op.get("target_path"), list):
            step = op.get("step", binding.get("step"))
            requests = _requests(ir)
            if not isinstance(step, int) or not (0 <= step < len(requests)):
                return False, "step 越界"
            tokens = list(op["target_path"])
            if _path_lookup(requests[step].get("body"), tokens) is _PATH_MISSING:
                return False, "target_path 不存在"
            shown = next((path for path, item_tokens, _sv, _raw in _leaf_paths(requests[step].get("body"))
                          if list(item_tokens) == tokens), "")
            target_path = shown
            location = (step, tokens, None)
        else:
            location = _locate_path(ir, target_path, op.get("step"))
        if not location or not target_path:
            return False, "target_path 不存在或有歧义"
        step, tokens, _raw = location
        binding.update({"target_path": target_path, "target_tokens": tokens, "step": step})
        input_item = _input(ir, param)
        if input_item is not None:
            input_item.update({"path": target_path, "tokens": tokens, "step": step})
        return True, "ok"

    if name == "parameterize":
        param = str(op.get("param") or op.get("param_name") or "")
        path = op.get("path") or op.get("target_path")
        if not param or not path or _input(ir, param):
            return False, "缺 path/param 或参数已存在"
        if isinstance(path, list):
            step = op.get("step")
            if not isinstance(step, int) or not (0 <= step < len(_requests(ir))):
                return False, "token path 需要合法 step"
            tokens = list(path)
            raw = _path_lookup(_requests(ir)[step].get("body"), tokens)
            if raw is _PATH_MISSING:
                return False, "path 不存在"
            shown = next((shown for shown, item_tokens, _sv, _raw in _leaf_paths(_requests(ir)[step].get("body"))
                          if list(item_tokens) == tokens), "")
            path = shown
        else:
            location = _locate_path(ir, str(path), op.get("step"))
            if not location:
                return False, "path 不存在或有歧义"
            step, tokens, raw = location
        if not path:
            return False, "无法生成展示路径"
        ir.setdefault("inputs", []).append({
            "name": param,
            "path": str(path),
            "tokens": tokens,
            "step": step,
            "type": _infer_type(raw, str(tokens[-1] if tokens else path)),
            "required": True,
            "sample": copy.deepcopy(raw),
            "submit_mode": "raw",
            "selected_default": True,
        })
        ir.setdefault("bindings", []).append({
            "input": param,
            "target_path": str(path),
            "target_tokens": tokens,
            "step": step,
            "mode": "direct",
        })
        return True, "ok"

    if name == "set_identity":
        path = op.get("path")
        source = str(op.get("source") or "")
        if not path or not source:
            return False, "缺 path/source"
        if isinstance(path, list):
            step = op.get("step")
            if not isinstance(step, int) or not (0 <= step < len(_requests(ir))):
                return False, "token path 需要合法 step"
            tokens = list(path)
            if _path_lookup(_requests(ir)[step].get("body"), tokens) is _PATH_MISSING:
                return False, "path 不存在"
            shown = next((shown for shown, item_tokens, _sv, _raw in _leaf_paths(_requests(ir)[step].get("body"))
                          if list(item_tokens) == tokens), "")
            path = shown
        else:
            location = _locate_path(ir, str(path), op.get("step"))
            if not location:
                return False, "path 不存在或有歧义"
            step, tokens, _raw = location
        removed_names = [item.get("input") for item in (ir.get("bindings") or [])
                         if item.get("step") == step and list(item.get("target_tokens") or []) == list(tokens)]
        ir["bindings"] = [item for item in (ir.get("bindings") or [])
                          if not (item.get("step") == step and list(item.get("target_tokens") or []) == list(tokens))]
        for input_name in removed_names:
            _remove_unbound_input(ir, str(input_name or ""))
        ir.setdefault("identity", []).append({
            "path": str(path),
            "tokens": tokens,
            "step": step,
            "source": source,
            "evidence": [f"request://body.{path}", f"identity://{source}"],
        })
        return True, "ok"

    if name == "set_success_rule":
        field = op.get("field")
        values = list(op.get("ok_values") or [])
        if not field or not values:
            return False, "缺 field/ok_values"
        ir["success"] = {"field": field, "ok_values": values}
        return True, "ok"

    if name == "set_fact_check":
        endpoint = op.get("endpoint")
        match_field = op.get("match_field")
        param = op.get("param")
        if not endpoint or not match_field or not param or _input(ir, str(param)) is None:
            return False, "fact_check 缺 endpoint/match_field/有效 param"
        ir["fact_check"] = {"endpoint": endpoint, "match_field": match_field, "param": param}
        return True, "ok"

    if name == "set_source_binding":
        param = str(op.get("param") or "")
        source_id = str(op.get("source_id") or "")
        if _input(ir, param) is None:
            return False, f"参数 {param} 不存在"
        if not any(source.get("id") == source_id for source in (ir.get("sources") or [])):
            return False, f"source {source_id} 不存在"
        bindings = _bindings(ir, param)
        if not bindings:
            return False, f"参数 {param} 无绑定"
        for binding in bindings:
            binding["source_id"] = source_id
            binding["mode"] = op.get("mode") or "select_value"
            if op.get("target_key"):
                binding["target_key"] = op["target_key"]
        input_item = _input(ir, param)
        input_item["source_id"] = source_id
        input_item["type"] = "array" if (op.get("mode") == "expand_array") else "select"
        input_item["submit_mode"] = "value[]" if (op.get("mode") == "expand_array") else "value"
        return True, "ok"

    if name == "set_option_query":
        source_id = str(op.get("source_id") or "")
        protocol = op.get("protocol")
        source = next((item for item in (ir.get("sources") or []) if item.get("id") == source_id), None)
        if source is None or not isinstance(protocol, dict):
            return False, "source 不存在或 protocol 非对象"
        source["query_protocol"] = copy.deepcopy(protocol)
        return True, "ok"

    if name == "link_step":
        target_step = op.get("target_step")
        source_step = op.get("source_step")
        requests = _requests(ir)
        if not (isinstance(target_step, int) and isinstance(source_step, int)
                and 0 <= source_step < target_step < len(requests)):
            return False, "source_step/target_step 非法"
        target_path = op.get("target_path")
        source_path = op.get("source_path")
        if not target_path or not source_path:
            return False, "缺 target_path/source_path"
        target_location = _locate_path(ir, str(target_path), target_step) if not isinstance(target_path, list) else None
        if isinstance(target_path, list):
            target_tokens = list(target_path)
            if _path_lookup(requests[target_step].get("body"), target_tokens) is _PATH_MISSING:
                return False, "target_path 不存在"
            target_shown = next((shown for shown, tokens, _sv, _raw in _leaf_paths(requests[target_step].get("body"))
                                 if list(tokens) == target_tokens), "")
        elif target_location:
            _step, target_tokens, _raw = target_location
            target_shown = str(target_path)
        else:
            return False, "target_path 不存在或有歧义"
        source_tokens = list(op.get("source_tokens") or (source_path if isinstance(source_path, list) else []))
        if not source_tokens:
            source_tokens = [part for part in str(source_path).split(".") if part]
        source_response = requests[source_step].get("response_json")
        if source_response is not None and _path_lookup(source_response, source_tokens) is _PATH_MISSING:
            return False, "source_path 在来源步响应里不存在"
        _execution(ir).setdefault("links", []).append({
            "target_step": target_step,
            "target_path": target_shown,
            "target_tokens": target_tokens,
            "source_step": source_step,
            "source_path": str(source_path) if not isinstance(source_path, list) else "",
            "source_tokens": source_tokens,
        })
        return True, "ok"

    if name == "drop_step":
        requests = _requests(ir)
        step = op.get("step")
        if not isinstance(step, int) or not (0 <= step < len(requests)) or len(requests) == 1:
            return False, "step 越界或不能删除唯一请求"
        del requests[step]
        for collection in (ir.get("inputs") or [], ir.get("bindings") or [], ir.get("identity") or [], ir.get("constants") or [], ir.get("derived") or []):
            collection[:] = [item for item in collection if item.get("step") != step]
            for item in collection:
                if isinstance(item.get("step"), int) and item["step"] > step:
                    item["step"] -= 1
        links = []
        for link in _execution(ir).get("links") or []:
            if link.get("target_step") == step or link.get("source_step") == step:
                continue
            link = copy.deepcopy(link)
            if isinstance(link.get("target_step"), int) and link["target_step"] > step:
                link["target_step"] -= 1
            if isinstance(link.get("source_step"), int) and link["source_step"] > step:
                link["source_step"] -= 1
            links.append(link)
        _execution(ir)["links"] = links
        _execution(ir)["kind"] = "workflow" if len(requests) > 1 else "single"
        _renumber_steps(ir)
        return True, "ok"

    if name == "reorder_steps":
        requests = _requests(ir)
        order = op.get("order")
        if not isinstance(order, list) or sorted(order) != list(range(len(requests))):
            return False, "order 非合法排列"
        old = list(requests)
        positions = {old_index: new_index for new_index, old_index in enumerate(order)}
        requests[:] = [old[index] for index in order]
        for collection in (ir.get("inputs") or [], ir.get("bindings") or [], ir.get("identity") or [], ir.get("constants") or [], ir.get("derived") or []):
            for item in collection:
                if isinstance(item.get("step"), int):
                    item["step"] = positions[item["step"]]
        for link in _execution(ir).get("links") or []:
            link["target_step"] = positions.get(link.get("target_step"), link.get("target_step"))
            link["source_step"] = positions.get(link.get("source_step"), link.get("source_step"))
            if not (isinstance(link.get("source_step"), int) and isinstance(link.get("target_step"), int)
                    and link["source_step"] < link["target_step"]):
                return False, "重排后数据依赖倒置"
        _renumber_steps(ir)
        return True, "ok"

    return False, "未处理"


def apply_ir_fix_ops(api_request: dict, ops: list[dict]) -> tuple[dict, list, list]:
    """Apply restricted patches to IR and return a freshly compiled artifact."""
    if not is_ir_authoritative(api_request):
        raise ValueError("api_request is not Transaction IR authoritative")
    ir = copy.deepcopy(api_request["transaction_ir"])
    ir.pop("authority", None)
    synchronize_constants(ir)
    compiled = compile_transaction_ir(ir)
    applied: list[dict] = []
    rejected: list[dict] = []

    for op in ops or []:
        before = copy.deepcopy(ir)
        baseline = set(self_check(compiled))
        ok, detail = _apply_one(ir, op)
        if not ok:
            ir = before
            rejected.append({**op, "ok": False, "detail": detail})
            continue
        synchronize_constants(ir)
        issues = validate_transaction_ir(ir)
        if issues:
            ir = before
            rejected.append({**op, "ok": False, "detail": "回滚(IR 校验失败):" + "; ".join(issues[:3])})
            continue
        try:
            candidate = compile_transaction_ir(ir)
        except Exception as exc:  # noqa: BLE001
            ir = before
            rejected.append({**op, "ok": False, "detail": f"回滚(编译失败):{exc}"})
            continue
        new_bad = set(self_check(candidate)) - baseline
        if new_bad:
            ir = before
            rejected.append({**op, "ok": False, "detail": "回滚(引入结构问题):" + "; ".join(sorted(new_bad)[:3])})
            continue
        compiled = candidate
        applied.append({**op, "ok": True, "detail": detail})

    return compile_transaction_ir(ir), applied, rejected
