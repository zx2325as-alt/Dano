from __future__ import annotations

from dano.execution.page.skill_interface import build_skill_interface


def test_public_skill_interface_hides_option_source_endpoint_and_auth() -> None:
    api_request = {
        "params": ["审批人"],
        "field_types": {"审批人": "enum"},
        "body_template": {"approverId": "{{审批人}}"},
        "selects": [{
            "param": "审批人",
            "path": "approverId",
            "tokens": ["approverId"],
            "source_url": "https://oa.internal/api/user/search",
            "source_method": "POST",
            "source_post_data": '{"deptId":7}',
            "source_content_type": "application/json",
            "source_auth_headers": {"Authorization": "Bearer secret"},
            "source_records_path": ["rows"],
            "value_key": "userId",
            "label_key": "nickName",
            "count": 20,
        }],
    }

    interface = build_skill_interface(api_request)
    source = next(iter(interface["source_schema"].values()))

    assert source["kind"] == "dynamic_options"
    assert source["dynamic"] is True
    assert source["supports_live_validation"] is True
    assert source["fields"] == ["审批人"]
    assert "url" not in source
    assert "method" not in source
    assert "value_key" not in source
    assert "label_key" not in source
    assert "source_post_data" not in source
    assert "source_auth_headers" not in source
    assert "option_filter" not in source


def test_input_schema_exposes_only_opaque_source_id() -> None:
    api_request = {
        "params": ["会议室"],
        "field_types": {"会议室": "enum"},
        "body_template": {"roomId": "{{会议室}}"},
        "selects": [{
            "param": "会议室",
            "path": "roomId",
            "tokens": ["roomId"],
            "source_url": "/api/room/list",
            "value_key": "id",
            "label_key": "name",
        }],
    }

    interface = build_skill_interface(api_request)
    field = interface["input_schema"]["properties"]["会议室"]

    assert field["x-options-source"] is True
    assert field["x-source-id"]
    assert "/api/room/list" not in str(field)
