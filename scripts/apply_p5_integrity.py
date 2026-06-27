from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


compiler_path = Path("back/dano/execution/page/ir_compiler.py")
compiler = compiler_path.read_text(encoding="utf-8")

old = '''    constants: list[dict] = []
    identity_locations = {(int(item.get("step", 0)), tuple(item.get("tokens") or [])) for item in identity_items}
    binding_locations = {(int(item.get("step", 0)), tuple(item.get("target_tokens") or [])) for item in bindings}
    for step, request in enumerate(request_specs):
        for path, tokens, _shown, raw in _leaf_paths(request["body"]):
            location = (step, tuple(tokens))
            if location in binding_locations or location in identity_locations:
                continue
            constants.append({"path": path, "tokens": list(tokens), "step": step,
                              "value": copy.deepcopy(raw), "reason": "captured_constant"})

    links = discover_step_links(requests) if workflow else []
'''
new = '''    # Top-level derived entries are reserved for scalar mirrors. Array counts are owned
    # by the array binding's derived_count_paths and applied by the select resolver.
    derived_items: list[dict] = []
    for item in ir.get("derived") or []:
        if not isinstance(item, dict) or item.get("kind") == "array_count":
            continue
        entry = copy.deepcopy(item)
        target_path = str(entry.get("target_path") or "")
        if not target_path:
            continue
        target_step, target_tokens, _target_raw = _tokens_for_path(
            request_specs, target_path, preferred_step=entry.get("step")
        )
        entry["step"] = target_step
        entry["target_tokens"] = list(entry.get("target_tokens") or target_tokens)
        source_path = str(entry.get("source_path") or "")
        if source_path:
            source_step, source_tokens, _source_raw = _tokens_for_path(
                request_specs, source_path, preferred_step=target_step
            )
            if source_step != target_step:
                raise ValueError("derived mirror source and target must be in the same request")
            entry["source_tokens"] = list(entry.get("source_tokens") or source_tokens)
        derived_items.append(entry)

    links = discover_step_links(requests) if workflow else []
'''
compiler = replace_once(compiler, old, new, "compiler constant partition")

old = '''        "constants": constants,
        "identity": identity_items,
        "execution": execution,
'''
new = '''        "constants": [],
        "identity": identity_items,
        "derived": derived_items,
        "execution": execution,
'''
compiler = replace_once(compiler, old, new, "compiler derived projection")

old = '''    if not ir.get("success"):
        ir["success"] = copy.deepcopy(last.get("success") or {})
    issues = validate_transaction_ir(ir)
'''
new = '''    if not ir.get("success"):
        ir["success"] = copy.deepcopy(last.get("success") or {})
    from dano.execution.page.ir_integrity_p5 import synchronize_constants

    synchronize_constants(ir)
    issues = validate_transaction_ir(ir)
'''
compiler = replace_once(compiler, old, new, "compiler synchronize constants")

old = '''    derived = []
    for item in ir.get("derived") or []:
        item_step = item.get("step")
        if isinstance(item_step, int) and item_step != step:
            continue
        target = item.get("target_tokens") or item.get("target_path")
        if target and _path_lookup(body, target) is not _PATH_MISSING:
            derived.append(copy.deepcopy(item))
'''
new = '''    derived = []
    for item in ir.get("derived") or []:
        if item.get("kind") == "array_count":
            continue
        item_step = item.get("step")
        if isinstance(item_step, int) and item_step != step:
            continue
        target = item.get("target_tokens") or item.get("target_path")
        if target and _path_lookup(body, target) is not _PATH_MISSING:
            derived.append(copy.deepcopy(item))
'''
compiler = replace_once(compiler, old, new, "compiler array count filtering")
compiler_path.write_text(compiler, encoding="utf-8")


repair_path = Path("back/dano/execution/page/ir_repair_p5.py")
repair = repair_path.read_text(encoding="utf-8")

old = '''from dano.execution.page.ir_compiler import compile_transaction_ir, is_ir_authoritative
from dano.execution.page.request_capture import _PATH_MISSING, _infer_type, _leaf_paths, _path_lookup, self_check
from dano.execution.page.transaction_ir import validate_transaction_ir
'''
new = '''from dano.execution.page.ir_compiler import compile_transaction_ir, is_ir_authoritative
from dano.execution.page.ir_integrity_p5 import synchronize_constants
from dano.execution.page.request_capture import _PATH_MISSING, _infer_type, _leaf_paths, _path_lookup, self_check
from dano.execution.page.transaction_ir import validate_transaction_ir
'''
repair = replace_once(repair, old, new, "repair imports")

old = '''        source_tokens = list(op.get("source_tokens") or (source_path if isinstance(source_path, list) else []))
        if not source_tokens:
            source_tokens = [part for part in str(source_path).split(".") if part]
        _execution(ir).setdefault("links", []).append({
'''
new = '''        source_tokens = list(op.get("source_tokens") or (source_path if isinstance(source_path, list) else []))
        if not source_tokens:
            source_tokens = [part for part in str(source_path).split(".") if part]
        source_response = requests[source_step].get("response_json")
        if source_response is not None and _path_lookup(source_response, source_tokens) is _PATH_MISSING:
            return False, "source_path 在来源步响应里不存在"
        _execution(ir).setdefault("links", []).append({
'''
repair = replace_once(repair, old, new, "repair link source validation")

old = '''    ir = copy.deepcopy(api_request["transaction_ir"])
    ir.pop("authority", None)
    compiled = compile_transaction_ir(ir)
'''
new = '''    ir = copy.deepcopy(api_request["transaction_ir"])
    ir.pop("authority", None)
    synchronize_constants(ir)
    compiled = compile_transaction_ir(ir)
'''
repair = replace_once(repair, old, new, "repair initial synchronization")

old = '''        ok, detail = _apply_one(ir, op)
        if not ok:
            rejected.append({**op, "ok": False, "detail": detail})
            continue
        issues = validate_transaction_ir(ir)
'''
new = '''        ok, detail = _apply_one(ir, op)
        if not ok:
            ir = before
            rejected.append({**op, "ok": False, "detail": detail})
            continue
        synchronize_constants(ir)
        issues = validate_transaction_ir(ir)
'''
repair = replace_once(repair, old, new, "repair failed-op rollback")
repair_path.write_text(repair, encoding="utf-8")
