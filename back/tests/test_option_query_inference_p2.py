from __future__ import annotations

import json

import dano.execution.page  # noqa: F401
from dano.execution.page import request_capture as rc
from dano.execution.page.dataflow import infer_request_transaction
from dano.execution.page.ir_compiler import compile_api_request_from_ir
from dano.execution.page.recorder import RecordSession
from dano.execution.page.transaction_ir import validate_transaction_ir


def _read(*, url: str, method: str = "POST", body=None, rows=None,
          extra: dict | None = None, ref: str = "read:1", ui=None) -> dict:
    data = {"data": {"rows": list(rows or [])}}
    if extra:
        data["data"].update(extra)
    return {
        "method": method,
        "url": url,
        "post_data": None if body is None else json.dumps(body, ensure_ascii=False),
        "content_type": "application/json",
        "records_path": ["data", "rows"],
        "json": data,
        "_capture_ref": ref,
        "_ui_evidence": list(ui or []),
    }


def test_remote_search_and_page_protocol_are_inferred_from_direct_evidence() -> None:
    reads = [
        _read(
            url="https://oa.example/api/users/search",
            body={"keyword": "张", "pageNo": 1, "pageSize": 20},
            rows=[{"id": 12, "name": "张经理"}],
            extra={"nextPage": 2, "hasMore": True, "total": 31},
            ref="read:2",
            ui=[{"ref": "ui:1", "op": "fill", "value": "张"}],
        ),
        _read(
            url="https://oa.example/api/users/search",
            body={"keyword": "张", "pageNo": 2, "pageSize": 20},
            rows=[{"id": 34, "name": "张主管"}],
            extra={"nextPage": 3, "hasMore": False, "total": 31},
            ref="read:4",
            ui=[{"ref": "ui:3", "op": "fill", "value": "张"}],
        ),
    ]

    selects = rc.suggest_selects(
        '{"approverId":12}',
        reads,
        {"审批人": "张经理"},
    )

    assert len(selects) == 1
    select = selects[0]
    protocol = select["option_query"]
    assert protocol["search"] == {
        "location": "json",
        "path": ["keyword"],
        "min_length": 0,
        "required": True,
    }
    assert protocol["pagination"] == {
        "mode": "page",
        "location": "json",
        "path": ["pageNo"],
        "size_location": "json",
        "size_path": ["pageSize"],
        "default_size": 20,
        "max_size": 100,
    }
    assert protocol["response"] == {
        "next_cursor_path": ["data", "nextPage"],
        "has_more_path": ["data", "hasMore"],
        "total_path": ["data", "total"],
    }
    assert select["option_query_inference"]["confidence"] >= 0.98
    refs = {
        ref
        for item in select["option_query_inference"]["evidence"]
        for ref in item["evidence_refs"]
    }
    assert {"ui:1", "ui:3", "read:2", "read:4"} <= refs


def test_get_query_is_normalized_and_search_path_is_typed() -> None:
    read = {
        "method": "GET",
        "url": "https://oa.example/api/users?q=张&limit=20",
        "json": {"rows": [{"id": 12, "name": "张经理"}]},
        "records_path": ["rows"],
        "_capture_ref": "read:2",
        "_ui_evidence": [{"ref": "ui:1", "op": "fill", "value": "张"}],
    }

    select = rc.suggest_selects(
        '{"approverId":12}',
        [read],
        {"审批人": "张经理"},
    )[0]

    assert select["source_url"] == "https://oa.example/api/users"
    assert select["source_query"] == {"q": "张", "limit": "20"}
    assert select["option_query"]["search"]["location"] == "query"
    assert select["option_query"]["search"]["path"] == ["q"]


def test_exact_validation_is_inferred_only_from_stable_id_evidence() -> None:
    read = _read(
        url="https://oa.example/api/users/by-id",
        body={"id": 12},
        rows=[{"id": 12, "name": "张经理"}],
        ref="read:1",
    )

    select = rc.suggest_selects(
        '{"approverId":12}',
        [read],
        {"审批人": "张经理"},
    )[0]

    assert select["option_query"] == {
        "validation": {"location": "json", "path": ["id"]}
    }
    assert select["option_query_inference"]["evidence"][0]["kind"] == "validation"


def test_dependency_is_inferred_from_another_recorded_select_value() -> None:
    reads = [
        {
            "method": "GET",
            "url": "https://oa.example/api/departments",
            "json": {"rows": [{"id": 7, "name": "研发部"}]},
            "records_path": ["rows"],
            "_capture_ref": "read:1",
        },
        _read(
            url="https://oa.example/api/users/search",
            body={"departmentId": 7},
            rows=[{"id": 12, "name": "张经理"}],
            ref="read:2",
        ),
    ]

    selects = rc.suggest_selects(
        '{"departmentId":7,"approverId":12}',
        reads,
        {"部门": "研发部", "审批人": "张经理"},
    )
    approver = next(item for item in selects if item["path"] == "approverId")

    assert approver["option_query"]["dependencies"] == [{
        "field": "部门",
        "field_path": "departmentId",
        "location": "json",
        "path": ["departmentId"],
        "required": True,
    }]
    assert approver["option_query_inference"]["confidence"] == 0.91


def test_static_option_source_is_not_given_an_invented_query_protocol() -> None:
    read = {
        "method": "GET",
        "url": "https://oa.example/api/users",
        "json": {"rows": [{"id": 12, "name": "张经理"}]},
        "records_path": ["rows"],
        "_capture_ref": "read:1",
    }

    select = rc.suggest_selects(
        '{"approverId":12}',
        [read],
        {"审批人": "张经理"},
    )[0]

    assert "option_query" not in select
    assert "option_query_inference" not in select


def test_compiler_and_transaction_ir_preserve_inferred_dependency_contract() -> None:
    chosen = {
        "method": "POST",
        "url": "https://oa.example/api/leave/submit",
        "post_data": '{"departmentId":7,"approverId":12,"reason":"回家"}',
    }
    reads = [
        {
            "method": "GET",
            "url": "https://oa.example/api/departments",
            "json": {"rows": [{"id": 7, "name": "研发部"}]},
            "records_path": ["rows"],
            "_capture_ref": "read:1",
        },
        _read(
            url="https://oa.example/api/users/search",
            body={"departmentId": 7},
            rows=[{"id": 12, "name": "张经理"}],
            ref="read:2",
        ),
    ]
    transaction = infer_request_transaction(
        chosen,
        [chosen],
        {"部门": "研发部", "审批人": "张经理", "原因": "回家"},
        reads,
    )

    compiled = compile_api_request_from_ir(
        chosen,
        {"departmentId": "部门", "approverId": "审批人", "reason": "原因"},
        selects=transaction["selects"],
        typed={"部门": 7, "审批人": 12, "原因": "回家"},
        transaction_ir=transaction["transaction_ir"],
    )

    approver = next(item for item in compiled["selects"] if item["param"] == "审批人")
    dependency = approver["option_query"]["dependencies"][0]
    assert dependency["field"] == "部门"
    assert "field_path" not in dependency

    source = next(
        item for item in compiled["transaction_ir"]["sources"]
        if item.get("query_protocol", {}).get("dependencies")
    )
    ir_dependency = source["query_protocol"]["dependencies"][0]
    assert ir_dependency["field"] == "部门"
    assert "field_path" not in ir_dependency
    assert source["inference"]["status"] == "inferred"
    assert compiled["transaction_ir"]["compile"]["query_source_count"] == 1
    assert validate_transaction_ir(compiled["transaction_ir"]) == []


def test_recorder_collects_bounded_ui_evidence_and_reset_clears_it() -> None:
    session = RecordSession()
    session._on_record(None, json.dumps({
        "op": "fill",
        "locator": "placeholder=搜索人员",
        "field": "搜索人员",
        "value": "张",
    }, ensure_ascii=False))

    assert session._option_ui_evidence[0]["ref"].startswith("ui:")
    assert session._option_ui_evidence[0]["value"] == "张"
    session.reset()
    assert session._option_ui_evidence == []
    assert session._option_capture_seq == 0
