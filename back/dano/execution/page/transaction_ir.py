"""Transaction-level IR for request-captured page skills.

Transaction IR is the business and execution source of truth. It owns public inputs,
option sources, request bindings, identities, constants, derived fields, workflow links,
assertions and the captured request skeleton. ``api_request`` is a deterministic projection
and must be reproducible from this document.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any

IR_VERSION = "transaction-ir/v1"
P5_COMPILER = "transaction-ir/p5"


def stable_source_id(url: str | None, value_key: str | None = "", label_key: str | None = "") -> str:
    raw = "|".join([url or "", value_key or "", label_key or ""])
    return "src_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


@dataclass
class SourceSpec:
    id: str
    kind: str
    url: str
    value_key: str = ""
    label_key: str = ""
    method: str = "GET"
    content_type: str = "application/json"
    post_data: Any = None
    query: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    records_path: list[str | int] = field(default_factory=list)
    query_protocol: dict = field(default_factory=dict)
    inference: dict = field(default_factory=dict)
    count: int | None = None
    options: list[dict] = field(default_factory=list)
    option_filter: dict | None = None
    evidence: list[str] = field(default_factory=list)


@dataclass
class InputSpec:
    name: str
    path: str
    tokens: list[str | int] = field(default_factory=list)
    type: str = "string"
    required: bool = True
    sample: Any = None
    source_id: str | None = None
    submit_mode: str = "raw"
    confidence: float | None = None
    selected_default: bool = False
    evidence: list[str] = field(default_factory=list)


@dataclass
class BindingSpec:
    input: str
    target_path: str
    target_tokens: list[str | int] = field(default_factory=list)
    mode: str = "direct"
    source_id: str | None = None
    target_key: str | None = None
    paired_id_path: str | None = None
    paired_id_tokens: list[str | int] = field(default_factory=list)
    item_template: dict | None = None
    expand_fields: list[str] = field(default_factory=list)
    derived_count_paths: list[dict] = field(default_factory=list)


@dataclass
class ConstantSpec:
    path: str
    tokens: list[str | int] = field(default_factory=list)
    value: Any = None
    reason: str = "captured_constant"


@dataclass
class IdentitySpec:
    path: str
    tokens: list[str | int] = field(default_factory=list)
    source: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass
class StepSpec:
    idx: int
    method: str
    path: str
    role: str = "write"


@dataclass
class TransactionIR:
    version: str = IR_VERSION
    method: str = "POST"
    url: str = ""
    path: str = ""
    inputs: list[InputSpec] = field(default_factory=list)
    sources: list[SourceSpec] = field(default_factory=list)
    bindings: list[BindingSpec] = field(default_factory=list)
    constants: list[ConstantSpec] = field(default_factory=list)
    identity: list[IdentitySpec] = field(default_factory=list)
    derived: list[dict] = field(default_factory=list)
    steps: list[StepSpec] = field(default_factory=list)
    execution: dict = field(default_factory=dict)
    success: dict = field(default_factory=dict)
    fact_check: dict = field(default_factory=dict)
    goal: dict = field(default_factory=dict)
    compile: dict = field(default_factory=dict)
    capture: dict = field(default_factory=dict)


def _strip_empty(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            cleaned = _strip_empty(child)
            if cleaned in (None, "", [], {}) and not (key == "records_path" and child == []):
                continue
            out[key] = cleaned
        return out
    if isinstance(value, list):
        return [_strip_empty(item) for item in value if _strip_empty(item) not in (None, "", [], {})]
    return value


def ir_to_dict(ir: TransactionIR) -> dict:
    return _strip_empty(asdict(ir))


def request_path(url: str | None) -> str:
    value = str(url or "")
    scheme = value.find("//")
    if scheme >= 0:
        slash = value.find("/", scheme + 2)
        value = value[slash:] if slash >= 0 else "/"
    return value or "/"


def _valid_tokens(path: Any, *, empty: bool = False) -> bool:
    if not isinstance(path, list) or (not path and not empty):
        return False
    return all(
        not isinstance(token, bool)
        and isinstance(token, (str, int))
        and (not isinstance(token, int) or token >= 0)
        and (not isinstance(token, str) or bool(token))
        for token in path
    )


def _valid_records_path(path: Any) -> bool:
    return _valid_tokens(path, empty=True)


def _validate_query_protocol(protocol: Any, prefix: str) -> list[str]:
    if protocol in (None, {}):
        return []
    if not isinstance(protocol, dict):
        return [f"{prefix} must be an object"]
    issues: list[str] = []
    for section in ("search", "pagination", "validation"):
        spec = protocol.get(section)
        if spec is None:
            continue
        if not isinstance(spec, dict):
            issues.append(f"{prefix}.{section} must be an object")
            continue
        if not _valid_tokens(spec.get("path")):
            issues.append(f"{prefix}.{section}.path must be a non-empty token path")
        location = spec.get("location") or "body"
        if location not in {"body", "json", "query", "form"}:
            issues.append(f"{prefix}.{section}.location is unsupported")
    pagination = protocol.get("pagination")
    response = protocol.get("response") or {}
    if isinstance(pagination, dict) and pagination.get("mode") == "cursor":
        if not isinstance(response, dict) or not _valid_tokens(response.get("next_cursor_path")):
            issues.append(f"{prefix}.response.next_cursor_path is required for cursor pagination")
    dependencies = protocol.get("dependencies") or []
    if not isinstance(dependencies, list):
        issues.append(f"{prefix}.dependencies must be an array")
    else:
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, dict):
                issues.append(f"{prefix}.dependencies[{index}] must be an object")
                continue
            if not dependency.get("field"):
                issues.append(f"{prefix}.dependencies[{index}].field is required")
            if not _valid_tokens(dependency.get("path")):
                issues.append(f"{prefix}.dependencies[{index}].path must be a non-empty token path")
    return issues


def validate_transaction_ir(ir: dict | None) -> list[str]:
    """Validate graph integrity before compilation, sealing or publication."""
    if not isinstance(ir, dict):
        return ["ir must be an object"]
    issues: list[str] = []
    if ir.get("version") != IR_VERSION:
        issues.append("version must be transaction-ir/v1")

    input_names: set[str] = set()
    for index, inp in enumerate(ir.get("inputs") or []):
        if not isinstance(inp, dict):
            issues.append(f"inputs[{index}] must be an object")
            continue
        name = str(inp.get("name") or "")
        path = str(inp.get("path") or "")
        if not name:
            issues.append(f"inputs[{index}].name is required")
        elif name in input_names:
            issues.append(f"inputs[{index}].name duplicates {name}")
        else:
            input_names.add(name)
        if not path:
            issues.append(f"inputs[{index}].path is required")
        if "tokens" in inp and not _valid_tokens(inp.get("tokens")):
            issues.append(f"inputs[{index}].tokens must be a non-empty token path")

    source_ids: set[str] = set()
    for index, source in enumerate(ir.get("sources") or []):
        if not isinstance(source, dict):
            issues.append(f"sources[{index}] must be an object")
            continue
        source_id = str(source.get("id") or "")
        if not source_id:
            issues.append(f"sources[{index}].id is required")
        elif source_id in source_ids:
            issues.append(f"sources[{index}].id duplicates {source_id}")
        else:
            source_ids.add(source_id)
        if not source.get("url"):
            issues.append(f"sources[{index}].url is required")
        method = str(source.get("method") or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH"}:
            issues.append(f"sources[{index}].method is unsupported")
        if "records_path" in source and not _valid_records_path(source.get("records_path")):
            issues.append(f"sources[{index}].records_path must be a token path or [] for a root list")
        issues.extend(_validate_query_protocol(source.get("query_protocol"), f"sources[{index}].query_protocol"))
        inference = source.get("inference")
        if inference:
            if not isinstance(inference, dict):
                issues.append(f"sources[{index}].inference must be an object")
            else:
                confidence = inference.get("confidence")
                if confidence is not None and not (
                    isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= confidence <= 1
                ):
                    issues.append(f"sources[{index}].inference.confidence must be between 0 and 1")

    for index, binding in enumerate(ir.get("bindings") or []):
        if not isinstance(binding, dict):
            issues.append(f"bindings[{index}] must be an object")
            continue
        name = str(binding.get("input") or "")
        if not name:
            issues.append(f"bindings[{index}].input is required")
        elif name not in input_names:
            issues.append(f"bindings[{index}].input references unknown input {name}")
        source_id = binding.get("source_id")
        if source_id and source_id not in source_ids:
            issues.append(f"bindings[{index}].source_id references unknown source {source_id}")
        if not binding.get("target_path"):
            issues.append(f"bindings[{index}].target_path is required")
        if "target_tokens" in binding and not _valid_tokens(binding.get("target_tokens")):
            issues.append(f"bindings[{index}].target_tokens must be a non-empty token path")
        if "paired_id_tokens" in binding and binding.get("paired_id_tokens") and not _valid_tokens(binding.get("paired_id_tokens")):
            issues.append(f"bindings[{index}].paired_id_tokens must be a token path")

    for index, identity in enumerate(ir.get("identity") or []):
        if not isinstance(identity, dict):
            issues.append(f"identity[{index}] must be an object")
            continue
        if not identity.get("path"):
            issues.append(f"identity[{index}].path is required")
        if not identity.get("source"):
            issues.append(f"identity[{index}].source is required")
        if "tokens" in identity and not _valid_tokens(identity.get("tokens")):
            issues.append(f"identity[{index}].tokens must be a non-empty token path")

    for index, item in enumerate(ir.get("derived") or []):
        if not isinstance(item, dict):
            issues.append(f"derived[{index}] must be an object")
        elif item.get("kind") in {"array_count", "mirror"} and not item.get("target_path"):
            issues.append(f"derived[{index}].target_path is required")

    compile_meta = ir.get("compile") or {}
    if compile_meta.get("compiler") == P5_COMPILER:
        execution = ir.get("execution")
        requests = execution.get("requests") if isinstance(execution, dict) else None
        if not isinstance(execution, dict):
            issues.append("execution must be an object for transaction-ir/p5")
        else:
            kind = execution.get("kind")
            if kind not in {"single", "workflow"}:
                issues.append("execution.kind must be single or workflow")
            if not isinstance(requests, list) or not requests:
                issues.append("execution.requests must be a non-empty array")
            else:
                if kind == "single" and len(requests) != 1:
                    issues.append("execution.kind=single requires exactly one request")
                if kind == "workflow" and len(requests) < 2:
                    issues.append("execution.kind=workflow requires at least two requests")
                for index, request in enumerate(requests):
                    if not isinstance(request, dict):
                        issues.append(f"execution.requests[{index}] must be an object")
                        continue
                    if not request.get("url") and not request.get("path"):
                        issues.append(f"execution.requests[{index}].url/path is required")
                    if not isinstance(request.get("body"), (dict, list)):
                        issues.append(f"execution.requests[{index}].body must be an object or array")
        if isinstance(requests, list) and requests:
            from dano.execution.page.ir_integrity_p5 import integrity_issues

            issues.extend(integrity_issues(ir))
    return issues
