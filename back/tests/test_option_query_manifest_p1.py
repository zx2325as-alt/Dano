from __future__ import annotations

from dano.catalog.option_query_manifest_p1 import option_query_schema


def test_manifest_projection_exposes_capabilities_not_source_details() -> None:
    select = {
        "source_url": "https://oa.example/api/users/search",
        "source_post_data": {"keyword": "", "token": "must-not-leak"},
        "source_headers": {"X-Tenant": "t1"},
        "option_query": {
            "search": {"location": "json", "path": ["keyword"], "min_length": 2},
            "validation": {"location": "json", "path": ["id"]},
            "pagination": {"mode": "cursor", "location": "json", "path": ["cursor"]},
            "dependencies": [
                {"field": "部门", "location": "json", "path": ["departmentId"]},
                {"field": "部门", "location": "json", "path": ["departmentId"]},
                {"field": "公司", "location": "query", "path": ["companyId"]},
            ],
        },
    }

    projected = option_query_schema(select)

    assert projected == {
        "x-options-search": True,
        "x-options-depends-on": ["部门", "公司"],
        "x-options-validation": True,
        "x-options-min-query-length": 2,
        "x-options-pagination": "cursor",
    }
    rendered = repr(projected)
    assert "source_url" not in rendered
    assert "must-not-leak" not in rendered
    assert "X-Tenant" not in rendered


def test_manifest_projection_handles_absent_or_invalid_protocol() -> None:
    assert option_query_schema(None) == {}
    assert option_query_schema({}) == {}
    assert option_query_schema({"option_query": "bad"}) == {}
