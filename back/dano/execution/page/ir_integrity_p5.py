"""Cross-cutting integrity checks for authoritative Transaction IR.

The executable request skeleton owns captured values. Constants are a derived partition of
that skeleton: every leaf must be exactly one of input/select binding, identity, system
value, derived value, workflow override or captured constant. This module computes that
partition and validates all token locations against the captured request structures.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from dano.execution.page.request_capture import _PATH_MISSING, _leaf_paths, _path_lookup


def _step(item: dict) -> int:
    value = item.get("step", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _tokens(item: dict, key: str, fallback: str = "tokens") -> tuple:
    value = item.get(key)
    if not isinstance(value, list):
        value = item.get(fallback)
    return tuple(value) if isinstance(value, list) else ()


def _under(tokens: tuple, root: tuple) -> bool:
    return bool(root) and len(tokens) >= len(root) and tokens[:len(root)] == root


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _requests(ir: dict) -> list[dict]:
    execution = ir.get("execution") or {}
    requests = execution.get("requests") or []
    return requests if isinstance(requests, list) else []


def _dynamic_locations(ir: dict) -> tuple[set[tuple[int, tuple]], set[tuple[int, tuple]]]:
    exact: set[tuple[int, tuple]] = set()
    roots: set[tuple[int, tuple]] = set()

    for binding in ir.get("bindings") or []:
        if not isinstance(binding, dict):
            continue
        step = _step(binding)
        target = _tokens(binding, "target_tokens")
        if target:
            (roots if binding.get("mode") == "expand_array" else exact).add((step, target))
        paired = _tokens(binding, "paired_id_tokens")
        if paired:
            exact.add((step, paired))
        for derived in binding.get("derived_count_paths") or []:
            if isinstance(derived, dict):
                tokens = _tokens(derived, "tokens")
                if tokens:
                    exact.add((step, tokens))

    for item in ir.get("identity") or []:
        if isinstance(item, dict):
            tokens = _tokens(item, "tokens")
            if tokens:
                exact.add((_step(item), tokens))

    for item in ir.get("derived") or []:
        if isinstance(item, dict):
            tokens = _tokens(item, "target_tokens")
            if tokens:
                exact.add((_step(item), tokens))

    for step, request in enumerate(_requests(ir)):
        for item in request.get("system_values") or []:
            if isinstance(item, dict):
                tokens = _tokens(item, "tokens")
                if tokens:
                    exact.add((step, tokens))

    for link in ((ir.get("execution") or {}).get("links") or []):
        if not isinstance(link, dict):
            continue
        target_step = link.get("target_step")
        target = _tokens(link, "target_tokens")
        if isinstance(target_step, int) and target:
            exact.add((target_step, target))
    return exact, roots


def expected_constants(ir: dict) -> list[dict]:
    """Derive the canonical captured-constant partition from execution requests."""
    exact, roots = _dynamic_locations(ir)
    constants: list[dict] = []
    for step, request in enumerate(_requests(ir)):
        body = request.get("body") if isinstance(request, dict) else None
        if not isinstance(body, (dict, list)):
            continue
        for path, tokens, _shown, raw in _leaf_paths(body):
            location = (step, tuple(tokens))
            if location in exact or any(root_step == step and _under(location[1], root) for root_step, root in roots):
                continue
            constants.append({
                "path": path,
                "tokens": list(tokens),
                "step": step,
                "value": copy.deepcopy(raw),
                "reason": "captured_constant",
            })
    return constants


def synchronize_constants(ir: dict) -> None:
    ir["constants"] = expected_constants(ir)


def _location_label(step: int, tokens: tuple) -> str:
    return f"step={step},tokens={list(tokens)!r}"


def integrity_issues(ir: dict) -> list[str]:
    """Validate graph locations, ownership conflicts and canonical constants."""
    issues: list[str] = []
    requests = _requests(ir)
    count = len(requests)
    owners: dict[tuple[int, tuple], str] = {}

    def own(kind: str, index: int, step: int, tokens: tuple, *, root: bool = False) -> None:
        if step < 0 or step >= count:
            issues.append(f"{kind}[{index}].step is out of range")
            return
        if not tokens:
            issues.append(f"{kind}[{index}] token path is required for transaction-ir/p5")
            return
        body = requests[step].get("body") if isinstance(requests[step], dict) else None
        if _path_lookup(body, list(tokens)) is _PATH_MISSING:
            issues.append(f"{kind}[{index}] target is missing in execution request: {_location_label(step, tokens)}")
            return
        location = (step, tokens)
        previous = owners.get(location)
        if previous:
            issues.append(f"{kind}[{index}] conflicts with {previous} at {_location_label(step, tokens)}")
        else:
            owners[location] = f"{kind}[{index}]" + (" root" if root else "")

    for index, binding in enumerate(ir.get("bindings") or []):
        if not isinstance(binding, dict):
            issues.append(f"bindings[{index}] must be an object")
            continue
        own("bindings", index, _step(binding), _tokens(binding, "target_tokens"),
            root=binding.get("mode") == "expand_array")
        paired = _tokens(binding, "paired_id_tokens")
        if paired:
            own("bindings.paired_id", index, _step(binding), paired)
        for derived_index, derived in enumerate(binding.get("derived_count_paths") or []):
            if isinstance(derived, dict) and _tokens(derived, "tokens"):
                own(f"bindings[{index}].derived_count_paths", derived_index,
                    _step(binding), _tokens(derived, "tokens"))

    for index, identity in enumerate(ir.get("identity") or []):
        if isinstance(identity, dict):
            own("identity", index, _step(identity), _tokens(identity, "tokens"))

    for index, derived in enumerate(ir.get("derived") or []):
        if isinstance(derived, dict) and _tokens(derived, "target_tokens"):
            own("derived", index, _step(derived), _tokens(derived, "target_tokens"))

    links = (ir.get("execution") or {}).get("links") or []
    for index, link in enumerate(links):
        if not isinstance(link, dict):
            issues.append(f"execution.links[{index}] must be an object")
            continue
        source_step = link.get("source_step")
        target_step = link.get("target_step")
        if not (isinstance(source_step, int) and isinstance(target_step, int)
                and 0 <= source_step < target_step < count):
            issues.append(f"execution.links[{index}] must point from an earlier valid step")
            continue
        target_tokens = _tokens(link, "target_tokens")
        own("execution.links", index, target_step, target_tokens)
        source_tokens = _tokens(link, "source_tokens")
        response = requests[source_step].get("response_json")
        if source_tokens and response is not None and _path_lookup(response, list(source_tokens)) is _PATH_MISSING:
            issues.append(f"execution.links[{index}] source is missing in captured response")

    expected = {
        (_step(item), _tokens(item, "tokens")): item
        for item in expected_constants(ir)
    }
    actual: dict[tuple[int, tuple], dict] = {}
    for index, item in enumerate(ir.get("constants") or []):
        if not isinstance(item, dict):
            issues.append(f"constants[{index}] must be an object")
            continue
        location = (_step(item), _tokens(item, "tokens"))
        if not location[1]:
            issues.append(f"constants[{index}] token path is required for transaction-ir/p5")
            continue
        if location in actual:
            issues.append(f"constants[{index}] duplicates {_location_label(*location)}")
        actual[location] = item

    for location, expected_item in expected.items():
        item = actual.get(location)
        if item is None:
            issues.append(f"constants missing {_location_label(*location)}")
        elif _stable(item.get("value")) != _stable(expected_item.get("value")):
            issues.append(f"constants value drift at {_location_label(*location)}")
    for location in actual.keys() - expected.keys():
        issues.append(f"constants contains dynamic or unknown location {_location_label(*location)}")
    return issues
