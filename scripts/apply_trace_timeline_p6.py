from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


recorder_path = Path("back/dano/execution/page/recorder.py")
text = recorder_path.read_text(encoding="utf-8")
text = replace_once(text, "import json\n", "import json\nimport time\n", "import time")

text = replace_once(
    text,
    '''        self.reads: list[dict] = []         # 抓到的读请求(GET+JSON 列表/字典)→ Q2 选领导等 select 的候选源
        self._on_step = on_step
''',
    '''        self.reads: list[dict] = []         # 抓到的读请求(GET+JSON 列表/字典)→ Q2 选领导等 select 的候选源
        self.timeline: list[dict] = []      # P6: UI/network 真实交错时间线
        self._timeline_seq = 0
        self._request_seq = 0
        self._last_ui_event_id: str | None = None
        self._request_events: dict[int, dict] = {}
        self._active_request: dict | None = None
        self._on_step = on_step
''',
    "init timeline",
)

marker = '''    async def start(self, start_url: str, *, base_url: str = "", headless: bool = True,
'''
methods = '''    def _record_timeline(self, event_type: str, payload: dict, *, request_id: str | None = None,
                         parent_event_id: str | None = None) -> dict:
        seq = self._timeline_seq
        self._timeline_seq += 1
        event = {
            "event_id": f"cap-{seq:06d}",
            "type": event_type,
            "sequence": seq,
            "monotonic_ns": time.monotonic_ns(),
            "wall_time_ns": time.time_ns(),
            "request_id": request_id,
            "parent_event_id": parent_event_id,
            "payload": payload,
        }
        self.timeline.append(event)
        if event_type == "ui":
            self._last_ui_event_id = event["event_id"]
        return event

    def _timeline_request(self, request) -> None:  # noqa: ANN001
        try:
            if getattr(request, "resource_type", "") not in ("xhr", "fetch"):
                return
            key = id(request)
            if key in self._request_events:
                return
            from dano.execution.page.capture_bundle import content_hash
            from dano.execution.page.request_capture import looks_like_auth_write, looks_like_read_request
            method = (request.method or "GET").upper()
            url = request.url or ""
            post_data = request.post_data
            role = "read"
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                if looks_like_auth_write(url, post_data):
                    role = "infra"
                elif not looks_like_read_request(url, post_data):
                    role = "write"
            request_id = f"req-{self._request_seq:06d}"
            self._request_seq += 1
            event = self._record_timeline(
                "network.request",
                {"method": method, "url": url, "role": role,
                 "has_body": post_data is not None,
                 "body_hash": content_hash(post_data) if post_data is not None else ""},
                request_id=request_id,
                parent_event_id=self._last_ui_event_id,
            )
            self._request_events[key] = {
                "request_id": request_id, "event_id": event["event_id"],
                "sequence": event["sequence"], "role": role,
                "method": method, "url": url,
            }
        except Exception:  # noqa: BLE001
            pass

'''
text = replace_once(text, marker, methods + marker, "timeline methods")

text = replace_once(
    text,
    '''        await self._context.expose_binding("__danoRecord", self._on_record)
        if self._intercept:
''',
    '''        await self._context.expose_binding("__danoRecord", self._on_record)
        self._context.on("request", self._timeline_request)
        if self._intercept:
''',
    "request observer",
)

text = replace_once(
    text,
    '''    def _capture(self, m: str, url: str, pd: str | None, ct: str, headers: dict | None = None) -> None:
        """登记一个写请求(含请求头,回放鉴权用)+ 实时推给前端诊断。"""
        if pd:
            self.requests.append({"method": m, "url": url, "post_data": pd,
                                  "content_type": ct, "headers": headers or {}})
''',
    '''    def _capture(self, m: str, url: str, pd: str | None, ct: str, headers: dict | None = None) -> None:
        """登记一个写请求(含请求头,回放鉴权用)+ 实时推给前端诊断。"""
        if pd:
            meta = self._active_request
            if meta is None:
                from dano.execution.page.capture_bundle import content_hash
                request_id = f"req-{self._request_seq:06d}"
                self._request_seq += 1
                event = self._record_timeline(
                    "network.request",
                    {"method": m, "url": url, "role": "write", "content_type": ct,
                     "has_body": True, "body_hash": content_hash(pd)},
                    request_id=request_id,
                    parent_event_id=self._last_ui_event_id,
                )
                meta = {"request_id": request_id, "event_id": event["event_id"],
                        "sequence": event["sequence"], "role": "write", "method": m, "url": url}
            self.requests.append({"method": m, "url": url, "post_data": pd,
                                  "content_type": ct, "headers": headers or {},
                                  "_capture_id": meta["request_id"],
                                  "_request_event_id": meta["event_id"],
                                  "_request_sequence": meta["sequence"]})
''',
    "capture metadata",
)

text = replace_once(
    text,
    '''            self._capture(m, request.url, request.post_data, hd.get("content-type", ""), hd)
''',
    '''            self._active_request = self._request_events.get(id(request))
            try:
                self._capture(m, request.url, request.post_data, hd.get("content-type", ""), hd)
            finally:
                self._active_request = None
''',
    "direct request correlation",
)

text = replace_once(
    text,
    '''                self._capture(m, url, pd, hd.get("content-type", ""), hd)
                await route.fulfill(status=200, content_type="application/json",
''',
    '''                self._active_request = self._request_events.get(id(request))
                try:
                    self._capture(m, url, pd, hd.get("content-type", ""), hd)
                finally:
                    self._active_request = None
                await route.fulfill(status=200, content_type="application/json",
''',
    "route correlation",
)

text = replace_once(
    text,
    '''            if m in ("POST", "PUT", "PATCH"):
                # 写请求的真实响应(taskId 等)→ 贴回第一个同 url、还没响应的已抓写请求,供 Q3 步间数据流发现
                for r in self.requests:
                    if r.get("url") == url and "response_json" not in r:
                        r["response_json"] = data
                        break
''',
    '''            meta = self._request_events.get(id(response.request))
            response_event = self._record_timeline(
                "network.response",
                {"method": m, "url": url, "role": (meta or {}).get("role"),
                 "content_type": ct, "status": response.status,
                 "has_response": True,
                 "response_hash": __import__("dano.execution.page.capture_bundle", fromlist=["content_hash"]).content_hash(data)},
                request_id=(meta or {}).get("request_id"),
                parent_event_id=(meta or {}).get("event_id"),
            )
            if m in ("POST", "PUT", "PATCH"):
                target = next((r for r in self.requests
                               if meta and r.get("_capture_id") == meta.get("request_id")), None)
                if target is None:
                    target = next((r for r in self.requests
                                   if r.get("url") == url and "response_json" not in r), None)
                if target is not None:
                    target["response_json"] = data
                    target["status"] = response.status
                    target["_response_event_id"] = response_event["event_id"]
                    target["_response_sequence"] = response_event["sequence"]
''',
    "response correlation",
)

text = replace_once(
    text,
    '''            self.reads.append({"method": m, "url": url, "status": response.status,
                               "json": data if len(self.reads) < 60 else None,
                               "count": len(items)})
''',
    '''            self.reads.append({"method": m, "url": url, "status": response.status,
                               "json": data if len(self.reads) < 60 else None,
                               "count": len(items),
                               "_capture_id": (meta or {}).get("request_id"),
                               "_request_event_id": (meta or {}).get("event_id"),
                               "_request_sequence": (meta or {}).get("sequence"),
                               "_response_event_id": response_event["event_id"],
                               "_response_sequence": response_event["sequence"]})
''',
    "read metadata",
)

text = replace_once(
    text,
    '''        # 同一 locator 连续 fill/select/pick(用户改了又改/逐字符)→ 覆盖,只留最后一次
''',
    '''        event = self._record_timeline("ui", dict(step))
        step["_capture_event_id"] = event["event_id"]
        step["_capture_sequence"] = event["sequence"]
        step["_observed_at_ns"] = event["monotonic_ns"]
        # 同一 locator 连续 fill/select/pick(用户改了又改/逐字符)→ 覆盖,只留最后一次
''',
    "ui timeline",
)

text = replace_once(
    text,
    '''    def captured_reads(self) -> list[dict]:
        return list(self.reads)
''',
    '''    def captured_reads(self) -> list[dict]:
        return list(self.reads)

    def captured_timeline(self) -> list[dict]:
        return [dict(event) for event in self.timeline]
''',
    "timeline accessor",
)

text = replace_once(
    text,
    '''        self.steps.clear()
''',
    '''        self.steps.clear()
        self.requests.clear()
        self.reads.clear()
        self.timeline.clear()
        self._timeline_seq = 0
        self._request_seq = 0
        self._last_ui_event_id = None
        self._request_events.clear()
        self._active_request = None
''',
    "reset capture segment",
)
recorder_path.write_text(text, encoding="utf-8")


gateway_path = Path("back/dano/gateway/app.py")
gateway = gateway_path.read_text(encoding="utf-8")
gateway = replace_once(
    gateway,
    '''                        writes=all_caps, reads=pending_reads, storage_state=login_state,
                        samples=pending_samples, required_labels=pending_required)
''',
    '''                        writes=all_caps, reads=pending_reads, timeline=sess.captured_timeline(),
                        storage_state=login_state, samples=pending_samples,
                        required_labels=pending_required)
''',
    "gateway timeline",
)
gateway_path.write_text(gateway, encoding="utf-8")
