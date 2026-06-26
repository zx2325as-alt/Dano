from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, new: str, label: str) -> str:
    left = text.find(start)
    if left < 0:
        raise RuntimeError(f"{label}: start marker not found")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:left] + new + text[right:]


def patch_gateway() -> None:
    path = "back/dano/gateway/app.py"
    text = read(path)
    text = replace_once(text, "from pydantic import BaseModel\n", "from pydantic import BaseModel, Field\n", "gateway Field import")
    start = "class ToolOptionsReq(BaseModel):\n"
    end = "\n\nclass ExportSkillsReq(BaseModel):\n"
    block = '''class ToolOptionsReq(BaseModel):
    name: str                       # 工具名(= skill_id 点转 __)
    field: str                      # 要列可选项的业务参数名
    query: str = Field(default="", max_length=200)
    context: dict = Field(default_factory=dict)   # 已填写的业务字段,用于级联候选
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=512)


@app.post("/v1/tools/options")
async def tool_options(req: ToolOptionsReq, x_tenant_key: str | None = Header(default=None)) -> dict:
    """受控候选查询入口。

    前端只提交 Skill、字段、搜索词和业务上下文。目标系统 URL、请求体、响应路径与凭证
    始终留在 Dano 后端；返回统一的 option-query/v1 label/value 分页结果。
    """
    tenant = await _auth_tenant(x_tenant_key)
    skill_id = skill_id_of(req.name)
    sub_str, _, action = skill_id.partition(".")
    if not action:
        raise HTTPException(status_code=400, detail="name 应能解析为 {subsystem}.{action}")
    orch = await _orchestrator(tenant)
    return await orch.list_field_options(
        Subsystem(sub_str), action, req.field, tenant=tenant,
        query=req.query, context=req.context, limit=req.limit, cursor=req.cursor)
'''
    text = replace_between(text, start, end, block, "gateway option route")
    write(path, text)


def patch_orchestrator() -> None:
    path = "back/dano/orchestrator/orchestrator.py"
    text = read(path)
    start = "    async def list_field_options(self, subsystem: Subsystem, action: str, field: str,\n"
    end = "    async def _run_page(self, task_id, skill, intent, *, confirm, tenant=\"\") -> TaskOutcome:  # noqa: ANN001\n"
    block = '''    async def list_field_options(self, subsystem: Subsystem, action: str, field: str,
                                 *, tenant: str = "", query: str = "", context: dict | None = None,
                                 limit: int = 50, cursor: str | None = None) -> dict:
        """Query one public page of live options through Dano's private source broker."""
        import json as _json

        from dano.execution.page.option_query import query_field_options
        from dano.execution.page.sessions import session_path_if_exists
        from dano.infra.http import tls_verify
        from dano.infra.token_store import get_token_headers, merge_auth_headers

        skill = self.registry.by_action(subsystem, action)
        if skill is None or not getattr(skill, "page_asset_id", None):
            return {"protocol_version": "option-query/v1", "field": field, "options": [],
                    "count": 0, "returned": 0, "source_status": "not_dynamic",
                    "has_more": False, "next_cursor": None,
                    "note": "未知动作或非页面型 Skill"}
        env = await self.store.get(skill.page_asset_id)
        api_request = (env.body or {}).get("api_request") if env else None
        if not api_request:
            return {"protocol_version": "option-query/v1", "field": field, "options": [],
                    "count": 0, "returned": 0, "source_status": "not_dynamic",
                    "has_more": False, "next_cursor": None,
                    "note": "该 Skill 没有可查询的请求来源"}

        scope = Scope(tenant=tenant, subsystem=skill.subsystem)
        profile = await self.store.get_published(AssetType.ENV_PROFILE, scope, asset_key="env_profile")
        base_url = ((profile.body.get("base_url") if profile else "") or "")
        storage = None
        session_path = session_path_if_exists(tenant, skill.subsystem.value)
        if session_path:
            try:
                storage = _json.loads(open(session_path, encoding="utf-8").read())
            except Exception:  # noqa: BLE001
                pass

        runtime_headers = await get_token_headers(tenant, skill.subsystem.value)
        if runtime_headers:
            api_request = merge_auth_headers(api_request, runtime_headers)
        return await query_field_options(
            api_request, field, base_url=base_url, storage_state=storage,
            verify=tls_verify(), query=query, context=context or {},
            limit=limit, cursor=cursor)

'''
    text = replace_between(text, start, end, block, "orchestrator option service")
    write(path, text)


def patch_manifest() -> None:
    path = "back/dano/catalog/manifest.py"
    text = read(path)
    text = replace_once(text, "from __future__ import annotations\n\n", "from __future__ import annotations\n\nimport copy\n\n", "manifest copy import")

    schema_start = "def _schema_prop(skill: SkillSpec, field: str, desc: str, sel: dict | None = None) -> dict:\n"
    schema_end = "\n\ndef _parameters_schema(skill: SkillSpec) -> dict:\n"
    schema_block = '''def _option_dependencies(sel: dict | None) -> list[str]:
    return sorted({
        str(binding.get("from"))[len("context."):]
        for binding in ((sel or {}).get("source_input_bindings") or [])
        if isinstance(binding, dict) and str(binding.get("from") or "").startswith("context.")
    })


def _dynamic_option_contract(prop: dict, sel: dict | None) -> None:
    prop["x-options-source"] = True
    prop["x-options-protocol"] = "option-query/v1"
    prop["x-options-search"] = True
    prop["x-options-page-size"] = 50
    dependencies = _option_dependencies(sel)
    if dependencies:
        prop["x-option-depends-on"] = dependencies


def _schema_prop(skill: SkillSpec, field: str, desc: str, sel: dict | None = None) -> dict:
    """Build the public field contract without exposing or trusting stale dynamic snapshots."""
    declared = (getattr(skill, "field_types", {}) or {}).get(field)
    dynamic = bool((sel or {}).get("source_url"))
    if declared == "array" and sel and sel.get("kind") == "array":
        prop = {
            "type": "array", "items": {"type": "string"}, "format": "name-ref-list",
            "label": desc,
            "description": desc + "(多选字段:展示 label,调用时提交 value 数组)",
            "x-submit-mode": "value[]", "x-option-label": "label", "x-option-value": "value",
        }
        if dynamic:
            _dynamic_option_contract(prop, sel)
        else:
            options = _option_snapshots((sel or {}).get("options") or [])
            if options:
                prop["x-options"] = options
                if len(options) <= _OPTIONS_INLINE_MAX:
                    prop["items"]["enum"] = [option["value"] for option in options]
        return prop
    if declared == "enum":
        prop = {
            "type": "string", "format": "name-ref", "label": desc,
            "description": desc + "(选择型字段:展示 label,调用时提交 value)",
            "x-submit-mode": "value", "x-option-label": "label", "x-option-value": "value",
        }
        if dynamic:
            _dynamic_option_contract(prop, sel)
        else:
            options = _option_snapshots((sel or {}).get("options") or [])
            if options:
                prop["x-options"] = options
                if len(options) <= _OPTIONS_INLINE_MAX:
                    prop["enum"] = [option["value"] for option in options]
        return prop
    if declared == "datetime":
        return {"type": "string", "format": "date-time", "label": desc,
                "description": desc + "(日期时间;传 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:mm:ss`,由 Dano 转换目标格式)"}
    if declared == "date":
        return {"type": "string", "format": "date", "label": desc,
                "description": desc + "(日期;传 `YYYY-MM-DD`,由 Dano 转换目标格式)"}
    if declared in ("number", "integer", "boolean", "array", "object"):
        return {"type": declared, "label": desc, "description": desc}
    return {"type": "number" if is_numeric_field(field, desc, declared_type=declared) else "string",
            "label": desc, "description": desc}
'''
    text = replace_between(text, schema_start, schema_end, schema_block, "manifest schema property")

    interface_start = "def _skill_interface(skill: SkillSpec) -> dict:\n"
    interface_end = "\n\ndef _req_path(req: dict) -> str:\n"
    interface_block = '''def _public_skill_interface(interface: dict) -> dict:
    """Strip target-system implementation details from the catalog response."""
    if not interface:
        return {}
    raw_sources = dict(interface.get("source_schema") or {})
    public_sources: dict[str, dict] = {}
    for source_id, source in raw_sources.items():
        source = dict(source or {})
        dynamic = bool(source.get("dynamic") or source.get("has_runtime_source") or source.get("url"))
        public_sources[str(source_id)] = {
            "id": str(source.get("id") or source_id),
            "kind": "dynamic_options" if dynamic else "static_options",
            "fields": list(source.get("fields") or []),
            "submit_modes": list(source.get("submit_modes") or []),
            "dynamic": dynamic,
            "count_hint": source.get("count_hint", source.get("count")),
            "protocol": "option-query/v1" if dynamic else "inline",
            "supports_search": dynamic,
            "supports_pagination": dynamic,
        }
    public_bindings = []
    for binding in interface.get("bindings") or []:
        if not isinstance(binding, dict):
            continue
        public_bindings.append({
            key: copy.deepcopy(binding.get(key))
            for key in ("input", "mode", "source_id", "step")
            if binding.get(key) is not None
        })
    return {
        "version": "skill-interface/public-v2",
        "input_schema": copy.deepcopy(interface.get("input_schema") or {}),
        "source_schema": public_sources,
        "bindings": public_bindings,
        "success": {"configured": bool(interface.get("success"))},
        "provenance": {
            key: value for key, value in dict(interface.get("provenance") or {}).items()
            if key in {"transaction_ir_version", "capture_hash", "trace_hash"} and value
        },
    }


def _skill_interface(skill: SkillSpec) -> dict:
    """Return a public interface; raw endpoint and request metadata stay in assets."""
    interface = dict(getattr(skill, "skill_interface", {}) or {})
    if not interface:
        api_request = getattr(skill, "api_request", None) or {}
        interface = dict(api_request.get("skill_interface") or {})
        if not interface and api_request:
            try:
                from dano.execution.page.skill_interface import build_skill_interface
                interface = build_skill_interface(
                    api_request,
                    required_fields=list(getattr(skill, "required_fields", []) or []))
            except Exception:  # noqa: BLE001
                interface = {}
    return _public_skill_interface(interface)
'''
    text = replace_between(text, interface_start, interface_end, interface_block, "manifest public interface")
    write(path, text)


def patch_frontend_api() -> None:
    path = "skillfrontend/src/api/skills.ts"
    text = read(path)
    text = replace_once(
        text,
        '  "x-option-value"?: string;\n',
        '  "x-option-value"?: string;\n  "x-options-protocol"?: string;\n  "x-options-search"?: boolean;\n  "x-options-page-size"?: number;\n  "x-option-depends-on"?: string[];\n',
        "frontend option schema fields")
    text = replace_once(
        text,
        '  | "invalid_shape"\n  | "source_error";\n',
        '  | "invalid_shape"\n  | "invalid_cursor"\n  | "needs_context"\n  | "invalid_request"\n  | "unsupported_method"\n  | "source_conflict"\n  | "source_error";\n',
        "frontend option statuses")
    text = replace_once(
        text,
        '  note?: string;\n}\n',
        '  note?: string;\n  protocol_version?: string;\n  returned?: number;\n  has_more?: boolean;\n  next_cursor?: string | null;\n  dependencies?: string[];\n}\n\nexport interface ToolOptionsQuery {\n  query?: string;\n  context?: Record<string, unknown>;\n  limit?: number;\n  cursor?: string | null;\n}\n',
        "frontend option response")
    old = '''export async function listSkillOptions(skillId: string, field: string): Promise<ToolOptionsResponse> {
  const toolName = skillId.split(".").join("__");
  const { data } = await api.post("/v1/tools/options", { name: toolName, field });
  return data;
}
'''
    new = '''export async function listSkillOptions(
  skillId: string,
  field: string,
  query: ToolOptionsQuery = {},
): Promise<ToolOptionsResponse> {
  const toolName = skillId.split(".").join("__");
  const { data } = await api.post("/v1/tools/options", { name: toolName, field, ...query });
  return data;
}
'''
    text = replace_once(text, old, new, "frontend option API")
    write(path, text)


def patch_invoke_drawer() -> None:
    path = "skillfrontend/src/components/InvokeDrawer.tsx"
    text = read(path)
    text = replace_once(text, 'import { useEffect, useMemo, useState } from "react";\n',
                        'import { useEffect, useMemo, useRef, useState } from "react";\n',
                        "InvokeDrawer useRef")
    text = replace_once(text,
                        'const OPTION_NON_ERROR = new Set(["idle", "loading", "ok", "empty"]);\n',
                        'const OPTION_NON_ERROR = new Set(["idle", "loading", "ok", "empty", "needs_context"]);\n',
                        "InvokeDrawer non-error states")
    state_old = '''  const [optionCache, setOptionCache] = useState<Record<string, ToolOption[]>>({});
  const [optionLoading, setOptionLoading] = useState<Record<string, boolean>>({});
  const [optionState, setOptionState] = useState<Record<string, OptionLoadState>>({});
'''
    state_new = '''  const [optionCache, setOptionCache] = useState<Record<string, ToolOption[]>>({});
  const [optionLoading, setOptionLoading] = useState<Record<string, boolean>>({});
  const [optionState, setOptionState] = useState<Record<string, OptionLoadState>>({});
  const [optionCursor, setOptionCursor] = useState<Record<string, string | null>>({});
  const [optionHasMore, setOptionHasMore] = useState<Record<string, boolean>>({});
  const [optionQuery, setOptionQuery] = useState<Record<string, string>>({});
  const optionTimers = useRef<Record<string, number>>({});
  const optionRequestSeq = useRef<Record<string, number>>({});
'''
    text = replace_once(text, state_old, state_new, "InvokeDrawer option states")
    reset_old = '''      setOptionCache({});
      setOptionLoading({});
      setOptionState({});
'''
    reset_new = '''      setOptionCache({});
      setOptionLoading({});
      setOptionState({});
      setOptionCursor({});
      setOptionHasMore({});
      setOptionQuery({});
      Object.values(optionTimers.current).forEach((timer) => window.clearTimeout(timer));
      optionTimers.current = {};
      optionRequestSeq.current = {};
'''
    text = replace_once(text, reset_old, reset_new, "InvokeDrawer reset")

    start = "  const setVal = (k: string, v: unknown) => setValues((p) => ({ ...p, [k]: v }));\n\n"
    end = "  async function doInvoke(input: Record<string, unknown>) {\n"
    block = '''  const clearOptionFields = (fields: string[], clearValues = true) => {
    if (!fields.length) return;
    const targets = new Set(fields);
    setOptionCache((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => !targets.has(key))));
    setOptionState((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => !targets.has(key))));
    setOptionCursor((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => !targets.has(key))));
    setOptionHasMore((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => !targets.has(key))));
    setOptionQuery((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => !targets.has(key))));
    if (clearValues) {
      setValues((prev) => ({ ...prev, ...Object.fromEntries(fields.map((field) => [field, undefined])) }));
    }
  };

  const setVal = (key: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    const dependents = Object.entries(props)
      .filter(([field, prop]) => field !== key && (prop?.["x-option-depends-on"] || []).includes(key))
      .map(([field]) => field);
    clearOptionFields(dependents);
  };

  const mergeOptions = (current: ToolOption[], incoming: ToolOption[]): ToolOption[] => {
    const seen = new Set<string>();
    return [...current, ...incoming].filter((option) => {
      const key = `${option.label}\u0000${String(option.value)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  };

  async function loadOptions(
    key: string,
    p: JSONSchemaProperty,
    args: { force?: boolean; query?: string; append?: boolean } = {},
  ) {
    if (!skill || optionLoading[key]) return;
    const dynamic = !!p?.["x-options-source"];
    if (!dynamic) {
      if (!(key in optionCache)) setOptionCache((prev) => ({ ...prev, [key]: normalizeOptions(p) }));
      return;
    }
    const query = args.query ?? optionQuery[key] ?? "";
    const append = !!args.append;
    const sameQuery = query === (optionQuery[key] ?? "");
    if (!args.force && !append && sameQuery && OPTION_READY.has(optionState[key]?.status || "")) return;

    const seq = (optionRequestSeq.current[key] || 0) + 1;
    optionRequestSeq.current[key] = seq;
    setOptionLoading((prev) => ({ ...prev, [key]: true }));
    setOptionState((prev) => ({ ...prev, [key]: { status: "loading" } }));
    try {
      const context = Object.fromEntries(Object.entries(values).filter(([field, value]) => field !== key && value != null && value !== ""));
      const response = await listSkillOptions(skill.name, key, {
        query,
        context,
        limit: p?.["x-options-page-size"] || 50,
        cursor: append ? optionCursor[key] : null,
      });
      if (optionRequestSeq.current[key] !== seq) return;
      const status = response.source_status || ((response.options || []).length ? "ok" : "empty");
      if (OPTION_READY.has(status)) {
        setOptionCache((prev) => ({
          ...prev,
          [key]: append ? mergeOptions(prev[key] || [], response.options || []) : (response.options || []),
        }));
        setOptionCursor((prev) => ({ ...prev, [key]: response.next_cursor || null }));
        setOptionHasMore((prev) => ({ ...prev, [key]: !!response.has_more }));
        setOptionQuery((prev) => ({ ...prev, [key]: query }));
        setOptionState((prev) => ({
          ...prev,
          [key]: { status, message: response.note, httpStatus: response.http_status },
        }));
      } else if (status === "needs_context") {
        clearOptionFields([key]);
        setOptionState((prev) => ({
          ...prev,
          [key]: { status, message: response.note || "请先填写依赖字段", httpStatus: response.http_status },
        }));
      } else {
        clearOptionFields([key]);
        const detail = response.note || `候选来源不可用（${status}）`;
        setOptionState((prev) => ({
          ...prev,
          [key]: { status, message: detail, httpStatus: response.http_status },
        }));
        message.error(`${key}：${detail}`);
      }
    } catch (error: any) {
      if (optionRequestSeq.current[key] !== seq) return;
      clearOptionFields([key]);
      const detail = error?.response?.data?.detail || error.message || "候选来源请求失败";
      setOptionState((prev) => ({ ...prev, [key]: { status: "network_error", message: detail } }));
      message.error(`拉取 ${key} 候选失败：${detail}`);
    } finally {
      if (optionRequestSeq.current[key] === seq) {
        setOptionLoading((prev) => ({ ...prev, [key]: false }));
      }
    }
  }

  function scheduleOptionSearch(key: string, p: JSONSchemaProperty, query: string) {
    if (optionTimers.current[key]) window.clearTimeout(optionTimers.current[key]);
    setOptionQuery((prev) => ({ ...prev, [key]: query }));
    optionTimers.current[key] = window.setTimeout(() => {
      void loadOptions(key, p, { force: true, query });
    }, 300);
  }

  function loadMoreOptions(key: string, p: JSONSchemaProperty) {
    if (!optionHasMore[key] || !optionCursor[key] || optionLoading[key]) return;
    void loadOptions(key, p, { append: true, query: optionQuery[key] || "" });
  }

'''
    text = replace_between(text, start, end, block, "InvokeDrawer loader")

    text = replace_once(text,
                        '            action={<Button size="small" onClick={() => loadOptions(key, p, true)}>重试</Button>}\n',
                        '            action={<Button size="small" onClick={() => loadOptions(key, p, { force: true })}>重试</Button>}\n',
                        "InvokeDrawer retry")
    text = replace_once(text,
                        '            <Button type="link" size="small" onClick={() => loadOptions(key, p, true)}>重新加载</Button>\n',
                        '            <Button type="link" size="small" onClick={() => loadOptions(key, p, { force: true })}>重新加载</Button>\n',
                        "InvokeDrawer reload")
    select_old = '''          optionFilterProp="label"
          style={{ width: "100%" }}
          value={(values[key] as any) ?? undefined}
          options={options}
          loading={!!optionLoading[key]}
          status={sourceFailed ? "error" : undefined}
          placeholder={dynamic ? `打开下拉实时加载${label}` : key}
          notFoundContent={
            optionLoading[key] ? "正在加载候选…"
              : sourceFailed ? "候选来源不可用"
                : state?.status === "empty" ? "当前条件下没有可选项"
                  : dynamic ? "打开下拉加载实时候选" : "无可选项"
          }
          onFocus={() => loadOptions(key, p)}
          onDropdownVisibleChange={(open) => { if (open) loadOptions(key, p); }}
          onChange={(v) => setVal(key, v)}
'''
    select_new = '''          optionFilterProp="label"
          filterOption={dynamic ? false : undefined}
          style={{ width: "100%" }}
          value={(values[key] as any) ?? undefined}
          options={options}
          loading={!!optionLoading[key]}
          status={sourceFailed ? "error" : undefined}
          placeholder={dynamic ? `输入关键词搜索${label}` : key}
          notFoundContent={
            optionLoading[key] ? "正在加载候选…"
              : state?.status === "needs_context" ? (state.message || "请先填写依赖字段")
                : sourceFailed ? "候选来源不可用"
                  : state?.status === "empty" ? "当前条件下没有可选项"
                    : dynamic ? "输入关键词搜索或打开下拉加载" : "无可选项"
          }
          onFocus={() => loadOptions(key, p)}
          onDropdownVisibleChange={(open) => { if (open) loadOptions(key, p); }}
          onSearch={dynamic ? (query) => scheduleOptionSearch(key, p, query) : undefined}
          onPopupScroll={dynamic ? (event) => {
            const target = event.currentTarget as HTMLDivElement;
            if (target.scrollTop + target.clientHeight >= target.scrollHeight - 24) loadMoreOptions(key, p);
          } : undefined}
          onChange={(v) => setVal(key, v)}
'''
    text = replace_once(text, select_old, select_new, "InvokeDrawer Select")

    empty_hint = '''      } else if (dynamic && state?.status === "empty") {
        sourceHint = (
          <Space size={4} style={{ marginTop: 4 }}>
            <Typography.Text type="secondary">{state.message || "当前条件下没有可选项"}</Typography.Text>
            <Button type="link" size="small" onClick={() => loadOptions(key, p, { force: true })}>重新加载</Button>
          </Space>
        );
      }
'''
    context_hint = '''      } else if (dynamic && state?.status === "needs_context") {
        sourceHint = (
          <Alert style={{ marginTop: 6 }} type="info" showIcon message={state.message || "请先填写依赖字段"} />
        );
      } else if (dynamic && state?.status === "empty") {
        sourceHint = (
          <Space size={4} style={{ marginTop: 4 }}>
            <Typography.Text type="secondary">{state.message || "当前条件下没有可选项"}</Typography.Text>
            <Button type="link" size="small" onClick={() => loadOptions(key, p, { force: true })}>重新加载</Button>
          </Space>
        );
      }
'''
    text = replace_once(text, empty_hint, context_hint, "InvokeDrawer context hint")
    write(path, text)


def main() -> None:
    patch_gateway()
    patch_orchestrator()
    patch_manifest()
    patch_frontend_api()
    patch_invoke_drawer()


if __name__ == "__main__":
    main()
