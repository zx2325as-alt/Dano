"""Compile Transaction IR back to the existing api_request runtime shape."""

from __future__ import annotations

import copy
from typing import Any

from dano.execution.page.skill_interface import build_skill_interface
from dano.execution.page.request_capture import build_api_request, build_api_workflow


def _materialize_source_query(source: dict, name_by_path: dict[str, str]) -> None:
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


def _materialize_ir(transaction_ir: dict | None, param_map: dict | None,
                    selects: list[dict] | None, identity: list[dict] | None) -> dict:
    """Return a publish-time IR: user renames applied, only exposed inputs kept."""
    if not transaction_ir:
        return {}
    ir = copy.deepcopy(transaction_ir)
    param_map = param_map or {}
    selected_paths = set(param_map)
    select_paths = {s.get("path") for s in (selects or []) if s.get("path")}
    selected_paths |= select_paths

    name_by_path = {p: n for p, n in param_map.items() if p and n}
    kept_inputs: list[dict] = []
    kept_names: set[str] = set()
    kept_sources: set[str] = set()
    for inp in ir.get("inputs") or []:
        path = inp.get("path")
        if selected_paths and path not in selected_paths:
            continue
        if path in name_by_path:
            inp["name"] = name_by_path[path]
        kept_inputs.append(inp)
        if inp.get("name"):
            kept_names.add(inp["name"])
        if inp.get("source_id"):
            kept_sources.add(inp["source_id"])
    ir["inputs"] = kept_inputs

    bindings: list[dict] = []
    for binding in ir.get("bindings") or []:
        if binding.get("target_path") in name_by_path:
            binding["input"] = name_by_path[binding["target_path"]]
        if (not kept_names) or binding.get("input") in kept_names:
            bindings.append(binding)
            if binding.get("source_id"):
                kept_sources.add(binding["source_id"])
    ir["bindings"] = bindings

    sources = [source for source in (ir.get("sources") or [])
               if not kept_sources or source.get("id") in kept_sources]
    for source in sources:
        _materialize_source_query(source, name_by_path)
    ir["sources"] = sources

    if identity is not None:
        ir["identity"] = copy.deepcopy(identity)
    ir.setdefault("compile", {})
    ir["compile"]["param_paths"] = sorted(selected_paths)
    ir["compile"]["query_source_count"] = sum(
        1 for source in sources if source.get("query_protocol")
    )
    return ir


def compile_api_request_from_ir(req: dict, param_map: dict, *, base_url: str = "",
                                selects: list[dict] | None = None,
                                identity: list[dict] | None = None,
                                typed: dict | None = None,
                                transaction_ir: dict | None = None) -> dict | None:
    api_request = build_api_request(req, param_map, base_url=base_url,
                                    selects=selects, identity=identity, typed=typed)
    if api_request is not None:
        ir = _materialize_ir(transaction_ir, param_map, selects, identity)
        if ir:
            api_request["transaction_ir"] = ir
        api_request["skill_interface"] = build_skill_interface(api_request)
    return api_request


def compile_api_workflow_from_ir(writes: list[dict], *, param_map: dict, base_url: str = "",
                                 selects: list[dict] | None = None,
                                 identity: list[dict] | None = None,
                                 typed: dict | None = None,
                                 transaction_ir: dict | None = None) -> dict:
    api_request = build_api_workflow(writes, param_map=param_map, base_url=base_url,
                                     selects=selects, identity=identity, typed=typed)
    ir = _materialize_ir(transaction_ir, param_map, selects, identity)
    if ir:
        api_request["transaction_ir"] = ir
    api_request["skill_interface"] = build_skill_interface(api_request)
    return api_request


def transaction_ir_of(api_request: dict | None) -> dict[str, Any]:
    return dict((api_request or {}).get("transaction_ir") or {})
