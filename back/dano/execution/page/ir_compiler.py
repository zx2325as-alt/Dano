"""Deterministic Transaction IR compiler.

The compiler no longer calls ``build_api_request`` or ``build_api_workflow``. Capture
arguments are first materialized into a complete private Transaction IR; every executable
``api_request`` is then projected only from that IR. Recompiling the same IR produces the
same executable artifact.
"""
from __future__ import annotations

import copy
from typing import Any

from dano.execution.page.skill_interface import build_skill_interface
from dano.execution.page.transaction_ir import (
    IR_VERSION,
    P5_COMPILER,
    request_path,
    stable_source_id,
    validate_transaction_ir,
)
from dano.execution.page.request_capture import (
    _PATH_MISSING,
    _SEG,
    _infer_type,
    _is_system_timestamp,
    _leaf_paths,
    _parse_body,
    _path_lookup,
    _set_by_path,
    discover_step_links,
    extract_auth_headers,
    infer_success_rule,
)


def _path_for_url(url: str, base_url: str = "") -> str:
    if base_url and url.startswith(base_url):
        return url[len(base_url):] or "/"
    return request_path(url)


def _request_spec(req: dict, base_url: str = "") -> dict:
    body = _parse_body(req.get("post_data"))
    if not isinstance(body, (dict, list)):
        raise ValueError("captured request body is not a supported JSON/form structure")
    url = str(req.get("url") or req.get("path") or "")
    system_values = []
    for path, tokens, _shown, raw in _leaf_paths(body):
        key = str(tokens[-1] if tokens else path)
        if _is_system_timestamp(key, raw):
            system_values.append({
                "path": path,
                "tokens": list(tokens),
                "kind": "now_ms" if len(str(raw)) == 13 else "now_s",
            })
    out = {
        "method": str(req.get("method") or "POST").upper(),
        "url": url,
        "path": _path_for_url(url, base_url),
        "content_type": req.get("content_type") or "application/json",
        "body": copy.deepcopy(body),
        "auth_headers": extract_auth_headers(req.get("headers")),
        "system_values": system_values,
    }
    if req.get("response_json") is not None:
        out["response_json"] = copy.deepcopy(req.get("response_json"))
        learned = infer_success_rule([{"json": req.get("response_json")}])
        if learned:
            out["success"] = learned
    return out


def _path_rows(body: Any, path: str) -> list[tuple[list, Any]]:
    return [(list(tokens), raw) for shown, tokens, _sv, raw in _leaf_paths(body) if shown == path]


def _tokens_for_path(requests: list[dict], path: str, *, preferred_step: int | None = None) -> tuple[int, list, Any]:
    matches: list[tuple[int, list, Any]] = []
    for index, request in enumerate(requests):
        if preferred_step is not None and index != preferred_step:
            continue
        for tokens, raw in _path_rows(request.get("body"), path):
            matches.append((index, tokens, raw))
    if len(matches) != 1:
        reason = "missing" if not matches else "ambiguous"
        raise ValueError(f"Transaction IR path {path!r} is {reason}; token path is required")
    return matches[0]


def _select_source(select: dict, previous: dict | None = None) -> dict:
    previous = previous or {}
    source_id = str(select.get("source_id") or previous.get("id") or stable_source_id(
        select.get("source_url"), select.get("value_key"), select.get("label_key")
    ))
    records_path = select.get("source_records_path")
    if not isinstance(records_path, list):
        records_path = previous.get("records_path")
    if not isinstance(records_path, list):
        records_path = []
    out = {
        **copy.deepcopy(previous),
        "id": source_id,
        "kind": previous.get("kind") or "http_list",
        "url": select.get("source_url") or previous.get("url") or "",
        "method": str(select.get("source_method") or previous.get("method") or "GET").upper(),
        "content_type": select.get("source_content_type") or previous.get("content_type") or "application/json",
        "post_data": copy.deepcopy(select.get("source_post_data", previous.get("post_data"))),
        "query": copy.deepcopy(select.get("source_query") or previous.get("query") or {}),
        "headers": copy.deepcopy(select.get("source_headers") or previous.get("headers") or {}),
        "records_path": list(records_path),
        "value_key": select.get("value_key") or previous.get("value_key") or "",
        "label_key": select.get("label_key") or previous.get("label_key") or "",
        "query_protocol": copy.deepcopy(select.get("option_query") or previous.get("query_protocol") or {}),
        "inference": copy.deepcopy(select.get("option_query_inference") or previous.get("inference") or {}),
        "options": copy.deepcopy(select.get("options") or previous.get("options") or []),
        "count": select.get("count", previous.get("count")),
        "option_filter": copy.deepcopy(select.get("option_filter") or previous.get("option_filter")),
        "evidence": copy.deepcopy(previous.get("evidence") or []),
    }
    return {key: value for key, value in out.items() if value is not None}


def _materialize_dependencies(source: dict, name_by_path: dict[str, str]) -> None:
    protocol = source.get("query_protocol")
    if not isinstance(protocol, dict):
        return
    for dependency in protocol.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        field_path = dependency.get("field_path")
        if field_path and name_by_path.get(field_path):
            dependency["field"] = name_by_path[field_path]
        dependency.pop("field_path", None)


def _select_by_path(selects: list[dict] | None) -> dict[str, dict]:
    return {str(item.get("path")): item for item in (selects or []) if isinstance(item, dict) and item.get("path")}


def _materialize_ir(
    transaction_ir: dict | None,
    *,
    requests: list[dict],
    param_map: dict | None,
    selects: list[dict] | None,
    identity: list[dict] | None,
    typed: dict | None,
    base_url: str,
    workflow: bool,
) -> dict:
    if not isinstance(transaction_ir, dict):
        raise ValueError("Transaction IR is required for P5 compilation")
    ir = copy.deepcopy(transaction_ir)
    ir.pop("authority", None)
    ir["version"] = IR_VERSION
    request_specs = [_request_spec(request, base_url) for request in requests]
    if not request_specs:
        raise ValueError("Transaction IR requires at least one executable request")

    param_map = {str(path): str(name) for path, name in (param_map or {}).items() if path and name}
    select_map = _select_by_path(selects)
    selected_paths = list(dict.fromkeys([*param_map.keys(), *select_map.keys()]))
    name_by_path = dict(param_map)
    for path, select in select_map.items():
        name_by_path.setdefault(path, str(select.get("param") or ""))
    previous_inputs = {str(item.get("path")): item for item in (ir.get("inputs") or []) if item.get("path")}

    inputs: list[dict] = []
    bindings: list[dict] = []
    previous_bindings = {str(item.get("target_path")): item for item in (ir.get("bindings") or []) if item.get("target_path")}
    previous_sources = {str(item.get("id")): item for item in (ir.get("sources") or []) if item.get("id")}
    sources: dict[str, dict] = {}

    for path in selected_paths:
        previous_input = copy.deepcopy(previous_inputs.get(path) or {})
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
        if not name:
            raise ValueError(f"Transaction IR input {path!r} has no business name")
        source_id = None
        if select:
            source = _select_source(select, previous_sources.get(str(previous_input.get("source_id") or "")))
            source_id = source["id"]
            sources[source_id] = source
        input_type = (
            "array" if select and select.get("kind") == "array"
            else "select" if select
            else previous_input.get("type") or _infer_type(raw, str(tokens[-1] if tokens else path))
        )
        sample = copy.deepcopy((typed or {}).get(name, previous_input.get("sample", raw)))
        input_item = {
            **previous_input,
            "name": name,
            "path": path,
            "tokens": tokens,
            "step": step,
            "type": input_type,
            "required": bool(previous_input.get("required", True)),
            "sample": sample,
            "source_id": source_id,
            "submit_mode": "value[]" if select and select.get("kind") == "array" else "value" if select else "raw",
            "selected_default": True,
        }
        inputs.append({key: value for key, value in input_item.items() if value is not None})

        previous_binding = copy.deepcopy(previous_bindings.get(path) or {})
        binding = {
            **previous_binding,
            "input": name,
            "target_path": str(select.get("array_path") or path) if select and select.get("kind") == "array" else path,
            "target_tokens": list(select.get("array_tokens") or tokens) if select else tokens,
            "step": step,
            "mode": "expand_array" if select and select.get("kind") == "array" else "select_value" if select else "direct",
            "source_id": source_id,
        }
        if select:
            binding["target_key"] = select.get("target_key") or select.get("value_key")
            if select.get("id_path") or select.get("id_tokens"):
                paired_path = select.get("id_path")
                paired_tokens = select.get("id_tokens")
                if not paired_tokens and paired_path:
                    _paired_step, paired_tokens, _raw = _tokens_for_path(request_specs, paired_path, preferred_step=step)
                binding["paired_id_path"] = paired_path
                binding["paired_id_tokens"] = list(paired_tokens or [])
            if select.get("kind") == "array":
                binding["item_template"] = copy.deepcopy(select.get("item_template") or {})
                binding["expand_fields"] = list((select.get("item_template") or {}).keys())
                binding["derived_count_paths"] = copy.deepcopy(select.get("derived_count_paths") or [])
        bindings.append({key: value for key, value in binding.items() if value is not None})

    for source in sources.values():
        _materialize_dependencies(source, name_by_path)

    identity_items: list[dict] = []
    selected_set = set(selected_paths)
    for item in (identity if identity is not None else ir.get("identity") or []):
        if not isinstance(item, dict) or not item.get("path") or item.get("path") in selected_set:
            continue
        entry = copy.deepcopy(item)
        preferred_step = entry.get("step")
        step, tokens, _raw = _tokens_for_path(request_specs, str(entry["path"]), preferred_step=preferred_step)
        entry["step"] = step
        entry["tokens"] = list(entry.get("tokens") or tokens)
        evidence = list(entry.get("evidence") or [f"request://body.{entry['path']}"])
        if entry.get("source") and f"identity://{entry['source']}" not in evidence:
            evidence.append(f"identity://{entry['source']}")
        entry["evidence"] = evidence
        identity_items.append(entry)

    constants: list[dict] = []
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
    execution = {
        "kind": "workflow" if workflow else "single",
        "requests": request_specs,
        "links": copy.deepcopy(links),
    }
    last = request_specs[-1]
    ir.update({
        "method": last["method"],
        "url": last["url"],
        "path": last["path"],
        "inputs": inputs,
        "sources": list(sources.values()),
        "bindings": bindings,
        "constants": constants,
        "identity": identity_items,
        "execution": execution,
        "steps": [
            {"idx": index, "method": request["method"], "path": request["path"],
             "role": "selected_write" if index == len(request_specs) - 1 else "write"}
            for index, request in enumerate(request_specs)
        ],
        "compile": {
            "compiler": P5_COMPILER,
            "source_of_truth": "transaction_ir",
            "param_paths": sorted(selected_paths),
            "query_source_count": sum(1 for source in sources.values() if source.get("query_protocol")),
        },
    })
    if not ir.get("success"):
        ir["success"] = copy.deepcopy(last.get("success") or {})
    issues = validate_transaction_ir(ir)
    if issues:
        raise ValueError("Transaction IR materialization failed: " + "; ".join(issues))
    return ir


def _binding_applies(binding: dict, request: dict, step: int) -> bool:
    bound_step = binding.get("step")
    if isinstance(bound_step, int) and bound_step != step:
        return False
    return _path_lookup(request.get("body"), binding.get("target_tokens") or binding.get("target_path")) is not _PATH_MISSING


def _source_select_meta(source: dict, binding: dict, input_item: dict) -> dict:
    meta = {
        "param": input_item["name"],
        "source_id": source.get("id"),
        "source_url": source.get("url"),
        "source_method": source.get("method") or "GET",
        "source_content_type": source.get("content_type") or "application/json",
        "source_post_data": copy.deepcopy(source.get("post_data")),
        "source_query": copy.deepcopy(source.get("query") or {}),
        "source_headers": copy.deepcopy(source.get("headers") or {}),
        "source_records_path": copy.deepcopy(source.get("records_path") or []),
        "value_key": source.get("value_key"),
        "label_key": source.get("label_key"),
        "options": copy.deepcopy(source.get("options") or []),
        "count": source.get("count"),
        "option_filter": copy.deepcopy(source.get("option_filter")),
        "option_query": copy.deepcopy(source.get("query_protocol") or {}),
        "option_query_inference": copy.deepcopy(source.get("inference") or {}),
        "path": binding.get("target_path"),
        "tokens": copy.deepcopy(binding.get("target_tokens") or []),
    }
    if binding.get("mode") == "expand_array":
        meta.update({
            "kind": "array",
            "array_path": binding.get("target_path"),
            "array_tokens": copy.deepcopy(binding.get("target_tokens") or []),
            "target_key": binding.get("target_key") or source.get("value_key"),
            "item_template": copy.deepcopy(binding.get("item_template") or {}),
            "derived_count_paths": copy.deepcopy(binding.get("derived_count_paths") or []),
            "submit_mode": "value[]",
        })
    else:
        meta["submit_mode"] = "value"
        if binding.get("paired_id_path") or binding.get("paired_id_tokens"):
            meta["id_path"] = binding.get("paired_id_path")
            meta["id_tokens"] = copy.deepcopy(binding.get("paired_id_tokens") or [])
    return {key: value for key, value in meta.items() if value is not None}


def _compile_one(ir: dict, request: dict, step: int) -> dict:
    body = copy.deepcopy(request["body"])
    input_by_name = {str(item.get("name")): item for item in (ir.get("inputs") or [])}
    source_by_id = {str(item.get("id")): item for item in (ir.get("sources") or [])}
    params: list[str] = []
    samples: dict[str, Any] = {}
    field_types: dict[str, str] = {}
    selects: list[dict] = []

    for binding in ir.get("bindings") or []:
        if not _binding_applies(binding, request, step):
            continue
        input_item = input_by_name.get(str(binding.get("input") or ""))
        if input_item is None:
            continue
        name = input_item["name"]
        pathlike = binding.get("target_tokens") or binding.get("target_path")
        raw = _path_lookup(body, pathlike)
        if raw is _PATH_MISSING:
            continue
        params.append(name)
        sample = copy.deepcopy(input_item.get("sample", raw))
        samples[name] = sample
        mode = binding.get("mode") or "direct"
        if mode != "expand_array":
            recorded = str(sample) if sample not in (None, "") else None
            shown = "" if raw is None else str(raw)
            if recorded and len(recorded) >= 2 and recorded != shown and isinstance(raw, str) and recorded in shown:
                before, _middle, after = shown.partition(recorded)
                _set_by_path(body, pathlike, {_SEG: [part for part in (before, {"$p": name}, after) if part != ""]})
            else:
                _set_by_path(body, pathlike, "{{" + name + "}}")
        input_type = str(input_item.get("type") or "string")
        field_types[name] = "array" if mode == "expand_array" else "enum" if mode == "select_value" else input_type
        source = source_by_id.get(str(binding.get("source_id") or ""))
        if source:
            selects.append(_source_select_meta(source, binding, input_item))

    identity = []
    for item in ir.get("identity") or []:
        item_step = item.get("step")
        if isinstance(item_step, int) and item_step != step:
            continue
        pathlike = item.get("tokens") or item.get("path")
        if _path_lookup(body, pathlike) is _PATH_MISSING:
            continue
        identity.append(copy.deepcopy(item))

    derived = []
    for item in ir.get("derived") or []:
        item_step = item.get("step")
        if isinstance(item_step, int) and item_step != step:
            continue
        target = item.get("target_tokens") or item.get("target_path")
        if target and _path_lookup(body, target) is not _PATH_MISSING:
            derived.append(copy.deepcopy(item))

    out = {
        "method": request["method"],
        "path": request["path"],
        "url": request["url"],
        "content_type": request.get("content_type") or "application/json",
        "body_template": body,
        "params": list(dict.fromkeys(params)),
        "sample_inputs": samples,
        "auth_headers": copy.deepcopy(request.get("auth_headers") or {}),
        "field_types": field_types,
        "selects": selects,
        "identity": identity,
        "system_values": copy.deepcopy(request.get("system_values") or []),
        "derived_fields": derived,
    }
    if request.get("response_json") is not None:
        out["response_json"] = copy.deepcopy(request.get("response_json"))
    success = request.get("success") or (ir.get("success") if step == len((ir.get("execution") or {}).get("requests") or []) - 1 else None)
    if success:
        out["success_rule"] = copy.deepcopy(success)
    return out


def _mark_option_references(api_request: dict) -> None:
    selects = []
    for step in api_request.get("steps") or [api_request]:
        selects.extend(item for item in (step.get("selects") or []) if item.get("source_url"))
    if not selects:
        return
    from dano.execution.page.option_reference_p3 import REFERENCE_VERSION, source_fingerprint

    api_request["option_reference"] = {
        "version": REFERENCE_VERSION,
        "required": True,
        "legacy_raw_values": False,
    }
    for select in selects:
        select["option_reference_required"] = True
        select["source_fingerprint"] = source_fingerprint(select)


def compile_transaction_ir(transaction_ir: dict) -> dict:
    """Compile an executable artifact using only Transaction IR."""
    ir = copy.deepcopy(transaction_ir)
    ir.pop("authority", None)
    issues = validate_transaction_ir(ir)
    if issues:
        raise ValueError("Transaction IR compile failed: " + "; ".join(issues))
    compile_meta = ir.get("compile") or {}
    if compile_meta.get("compiler") != P5_COMPILER or compile_meta.get("source_of_truth") != "transaction_ir":
        raise ValueError("Transaction IR is not materialized for the P5 compiler")

    execution = ir["execution"]
    requests = execution["requests"]
    compiled_steps = [_compile_one(ir, request, index) for index, request in enumerate(requests)]
    for link in execution.get("links") or []:
        target = link.get("target_step")
        if isinstance(target, int) and 0 <= target < len(compiled_steps):
            compiled_steps[target].setdefault("links", []).append({
                key: copy.deepcopy(value)
                for key, value in link.items()
                if key not in {"target_step"}
            })

    if execution.get("kind") == "workflow":
        last = compiled_steps[-1]
        api_request = {
            "steps": compiled_steps,
            "params": copy.deepcopy(last.get("params") or []),
            "sample_inputs": copy.deepcopy(last.get("sample_inputs") or {}),
            "field_types": copy.deepcopy(last.get("field_types") or {}),
        }
        if ir.get("success"):
            api_request["success_rule"] = copy.deepcopy(ir["success"])
            compiled_steps[-1]["success_rule"] = copy.deepcopy(ir["success"])
    else:
        api_request = compiled_steps[0]

    if ir.get("fact_check"):
        api_request["fact_check"] = copy.deepcopy(ir["fact_check"])
    if ir.get("goal"):
        api_request["goal"] = copy.deepcopy(ir["goal"])
    api_request["transaction_ir"] = ir
    _mark_option_references(api_request)
    api_request["skill_interface"] = build_skill_interface(api_request)
    return api_request


def canonicalize_api_request(api_request: dict) -> dict:
    """Move allowed post-compile assertions into IR, then discard every direct artifact edit."""
    ir = copy.deepcopy((api_request or {}).get("transaction_ir") or {})
    if (ir.get("compile") or {}).get("compiler") != P5_COMPILER:
        return copy.deepcopy(api_request)
    ir.pop("authority", None)
    if api_request.get("fact_check"):
        ir["fact_check"] = copy.deepcopy(api_request["fact_check"])
    if api_request.get("goal"):
        ir["goal"] = copy.deepcopy(api_request["goal"])
    success = api_request.get("success_rule")
    if not success and api_request.get("steps"):
        success = (api_request["steps"][-1] or {}).get("success_rule")
    if success:
        ir["success"] = copy.deepcopy(success)
    return compile_transaction_ir(ir)


def is_ir_authoritative(api_request: dict | None) -> bool:
    ir = (api_request or {}).get("transaction_ir") or {}
    compile_meta = ir.get("compile") or {}
    return compile_meta.get("compiler") == P5_COMPILER and compile_meta.get("source_of_truth") == "transaction_ir"


def compile_api_request_from_ir(req: dict, param_map: dict, *, base_url: str = "",
                                selects: list[dict] | None = None,
                                identity: list[dict] | None = None,
                                typed: dict | None = None,
                                transaction_ir: dict | None = None) -> dict | None:
    ir = _materialize_ir(
        transaction_ir,
        requests=[req],
        param_map=param_map,
        selects=selects,
        identity=identity,
        typed=typed,
        base_url=base_url,
        workflow=False,
    )
    return compile_transaction_ir(ir)


def compile_api_workflow_from_ir(writes: list[dict], *, param_map: dict, base_url: str = "",
                                 selects: list[dict] | None = None,
                                 identity: list[dict] | None = None,
                                 typed: dict | None = None,
                                 transaction_ir: dict | None = None) -> dict:
    ir = _materialize_ir(
        transaction_ir,
        requests=writes,
        param_map=param_map,
        selects=selects,
        identity=identity,
        typed=typed,
        base_url=base_url,
        workflow=True,
    )
    return compile_transaction_ir(ir)


def transaction_ir_of(api_request: dict | None) -> dict[str, Any]:
    return copy.deepcopy((api_request or {}).get("transaction_ir") or {})
