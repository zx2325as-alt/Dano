from pathlib import Path

path = Path("back/dano/execution/page/ir_compiler.py")
text = path.read_text(encoding="utf-8")
old = '''        previous_input = copy.deepcopy(previous_inputs.get(path) or {})
        select = select_map.get(path)
        preferred_step = previous_input.get("step")
        step, tokens, raw = _tokens_for_path(request_specs, path, preferred_step=preferred_step)
        name = str(name_by_path.get(path) or previous_input.get("name") or path).strip()
'''
new = '''        previous_input = copy.deepcopy(previous_inputs.get(path) or {})
        select = select_map.get(path)
        preferred_step = previous_input.get("step")
        if select and select.get("kind") == "array" and (select.get("array_tokens") or select.get("tokens")):
            candidate_tokens = list(select.get("array_tokens") or select.get("tokens"))
            matches = [
                (index, copy.deepcopy(_path_lookup(request["body"], candidate_tokens)))
                for index, request in enumerate(request_specs)
                if (preferred_step is None or index == preferred_step)
                and _path_lookup(request["body"], candidate_tokens) is not _PATH_MISSING
            ]
            if len(matches) != 1:
                raise ValueError(f"Transaction IR array path {path!r} is missing or ambiguous")
            step, raw = matches[0]
            tokens = candidate_tokens
        else:
            step, tokens, raw = _tokens_for_path(request_specs, path, preferred_step=preferred_step)
        name = str(name_by_path.get(path) or previous_input.get("name") or path).strip()
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one compiler block, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
