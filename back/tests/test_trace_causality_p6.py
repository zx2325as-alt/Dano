from __future__ import annotations

import json

import pytest

from dano.execution.page.capture_bundle import (
    build_capture_bundle,
    capture_integrity_issues,
    raw_reads,
    raw_writes,
)
from dano.execution.page.recorder import RecordSession
from dano.execution.page.trace_normalizer import event_for_request, normalize_capture_bundle


class FakeRequest:
    def __init__(self, method: str, url: str, post_data: str | None = None):
        self.method = method
        self.url = url
        self.post_data = post_data
        self.resource_type = "xhr"
        self.headers = {"content-type": "application/json", "Authorization": "secret-token"}


class FakeResponse:
    def __init__(self, request: FakeRequest, payload, status: int = 200):
        self.request = request
        self.url = request.url
        self.status = status
        self.headers = {"content-type": "application/json"}
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_recorder_preserves_interleaving_and_exact_repeated_url_correlation() -> None:
    session = RecordSession(intercept_submit=False)
    session._on_record(None, json.dumps({
        "op": "fill", "locator": "label=原因", "field": "原因", "value": "第一次", "required": True,
    }, ensure_ascii=False))

    first = FakeRequest("POST", "https://oa.example/api/save", '{"reason":"第一次"}')
    session._timeline_request(first)
    session._on_request(first)

    session._on_record(None, json.dumps({
        "op": "submit", "locator": "role=button[name=提交]", "field": "", "value": "",
    }, ensure_ascii=False))

    second = FakeRequest("POST", "https://oa.example/api/save", '{"reason":"第二次"}')
    session._timeline_request(second)
    session._on_request(second)

    await session._on_response(FakeResponse(second, {"code": 0, "data": {"id": "B"}}))
    await session._on_response(FakeResponse(first, {"code": 0, "data": {"id": "A"}}))

    assert session.requests[0]["response_json"]["data"]["id"] == "A"
    assert session.requests[1]["response_json"]["data"]["id"] == "B"
    assert session.requests[0]["_capture_id"] != session.requests[1]["_capture_id"]

    bundle = build_capture_bundle(
        steps=session.steps,
        writes=session.captured_requests(),
        reads=session.captured_reads(),
        timeline=session.captured_timeline(),
    )
    assert capture_integrity_issues(bundle) == []
    trace = normalize_capture_bundle(bundle)

    types = [event["type"] for event in trace["events"]]
    assert types == [
        "ui.fill", "network.write", "ui.submit", "network.write",
        "network.response", "network.response",
    ]
    first_write = trace["events"][1]
    second_write = trace["events"][3]
    assert first_write["caused_by"] == [trace["events"][0]["event_id"]]
    assert second_write["caused_by"] == [trace["events"][2]["event_id"]]
    assert trace["events"][4]["caused_by"] == [second_write["event_id"]]
    assert trace["events"][5]["caused_by"] == [first_write["event_id"]]
    assert event_for_request(trace, session.requests[0]) != event_for_request(trace, session.requests[1])


@pytest.mark.asyncio
async def test_post_query_is_a_read_and_keeps_request_response_pair() -> None:
    session = RecordSession(intercept_submit=False)
    request = FakeRequest("POST", "https://oa.example/api/queryUsers", '{"keyword":"张"}')
    session._timeline_request(request)
    await session._on_response(FakeResponse(request, {"data": [{"id": 1, "name": "张三"}]}))

    assert len(session.reads) == 1
    assert session.reads[0]["_capture_id"]
    bundle = build_capture_bundle(
        writes=session.captured_requests(), reads=session.captured_reads(), timeline=session.captured_timeline()
    )
    trace = normalize_capture_bundle(bundle)
    assert [event["type"] for event in trace["events"]] == ["network.read", "network.response"]
    assert trace["events"][1]["caused_by"] == [trace["events"][0]["event_id"]]
    assert raw_reads(bundle)[0]["response_json"]["data"][0]["name"] == "张三"


def test_capture_bundle_keeps_bodies_but_never_header_or_storage_values() -> None:
    write = {
        "method": "POST",
        "url": "https://oa.example/api/save",
        "post_data": '{"reason":"回家"}',
        "headers": {"Authorization": "Bearer hidden", "X-Tenant": "tenant-a"},
        "response_json": {"code": 0},
    }
    bundle = build_capture_bundle(
        writes=[write],
        storage_state={
            "cookies": [{"name": "sid", "value": "cookie-hidden"}],
            "origins": [{
                "origin": "https://oa.example",
                "localStorage": [{"name": "access_token", "value": "storage-hidden"}],
            }],
        },
    )

    text = json.dumps(bundle, ensure_ascii=False)
    assert "Bearer hidden" not in text
    assert "cookie-hidden" not in text
    assert "storage-hidden" not in text
    assert raw_writes(bundle)[0]["post_data"] == '{"reason":"回家"}'
    assert raw_writes(bundle)[0]["response_json"] == {"code": 0}
    assert bundle["writes"][0]["header_names"] == ["Authorization", "X-Tenant"]
    assert bundle["writes"][0]["credential_header_names"] == ["Authorization"]
    assert capture_integrity_issues(bundle) == []


def test_reset_starts_a_new_capture_segment() -> None:
    session = RecordSession()
    session._on_record(None, json.dumps({"op": "fill", "locator": "label=主题", "field": "主题", "value": "周会"}, ensure_ascii=False))
    session._capture("POST", "https://oa.example/api/save", '{"title":"周会"}', "application/json", {})
    assert session.timeline and session.requests

    session.reset()

    assert session.steps == []
    assert session.requests == []
    assert session.reads == []
    assert session.timeline == []
    assert session._timeline_seq == 0
