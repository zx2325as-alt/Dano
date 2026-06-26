from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}\n--- pattern ---\n{old}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


# Gateway public request contract.
replace_once(
    "back/dano/gateway/app.py",
    "from pydantic import BaseModel\n",
    "from pydantic import BaseModel, Field\n",
)
replace_once(
    "back/dano/gateway/app.py",
    '''class ToolOptionsReq(BaseModel):
    name: str                       # 工具名(= skill_id 点转 __)
    field: str                      # 要列可选项的**参数名**(选择型字段)
''',
    '''class ToolOptionsReq(BaseModel):
    name: str                       # 工具名(= skill_id 点转 __)
    field: str                      # 要列可选项的**参数名**(选择型字段)
    query: str | None = Field(default=None, max_length=256)
    cursor: str | int | None = None
    limit: int = Field(default=50, ge=1, le=100)
    context: dict = Field(default_factory=dict)
''',
)
replace_once(
    "back/dano/gateway/app.py",
    '''    return await orch.list_field_options(Subsystem(sub_str), action, req.field, tenant=tenant)
''',
    '''    return await orch.list_field_options(
        Subsystem(sub_str), action, req.field,
        tenant=tenant, query=req.query, cursor=req.cursor,
        limit=req.limit, context=req.context,
    )
''',
)

# Orchestrator transport to the common option runtime.
replace_once(
    "back/dano/orchestrator/orchestrator.py",
    '''    async def list_field_options(self, subsystem: Subsystem, action: str, field: str,
                                 *, tenant: str = "") -> dict:
''',
    '''    async def list_field_options(self, subsystem: Subsystem, action: str, field: str,
                                 *, tenant: str = "", query: str | None = None,
                                 cursor: str | int | None = None, limit: int = 50,
                                 context: dict | None = None) -> dict:
''',
)
replace_once(
    "back/dano/orchestrator/orchestrator.py",
    '''        return await fetch_field_options(apir, field, base_url=base_url, storage_state=storage,
                                         verify=tls_verify())
''',
    '''        return await fetch_field_options(
            apir, field, base_url=base_url, storage_state=storage,
            verify=tls_verify(), query=query, cursor=cursor,
            limit=limit, context=context,
        )
''',
)

# Frontend API types and request payload.
replace_once(
    "skillfrontend/src/api/skills.ts",
    '''export interface ToolOptionsResponse {
  field: string;
  count: number;
  options: ToolOption[];
  submit_mode?: string;
  source_status?: OptionSourceStatus | string;
  http_status?: number;
  note?: string;
  truncated?: boolean;
  deduplicated_count?: number;
  invalid_item_count?: number;
  conflict_count?: number;
}
''',
    '''export type OptionCursor = string | number;

export interface ToolOptionsQuery {
  query?: string;
  cursor?: OptionCursor;
  limit?: number;
  context?: Record<string, unknown>;
}

export interface ToolOptionsResponse {
  field: string;
  count: number;
  options: ToolOption[];
  submit_mode?: string;
  source_status?: OptionSourceStatus | string;
  http_status?: number;
  note?: string;
  truncated?: boolean;
  deduplicated_count?: number;
  invalid_item_count?: number;
  conflict_count?: number;
  search_supported?: boolean;
  depends_on?: string[];
  missing_dependencies?: string[];
  min_query_length?: number;
  next_cursor?: OptionCursor | null;
  has_more?: boolean;
  total?: number | null;
  pagination_mode?: "page" | "offset" | "cursor" | string;
}
''',
)
replace_once(
    "skillfrontend/src/api/skills.ts",
    '''export async function listSkillOptions(skillId: string, field: string): Promise<ToolOptionsResponse> {
  const toolName = skillId.split(".").join("__");
  const { data } = await api.post("/v1/tools/options", { name: toolName, field });
  return data;
}
''',
    '''export async function listSkillOptions(
  skillId: string,
  field: string,
  request: ToolOptionsQuery = {},
): Promise<ToolOptionsResponse> {
  const toolName = skillId.split(".").join("__");
  const { data } = await api.post("/v1/tools/options", { name: toolName, field, ...request });
  return data;
}
''',
)

# Frontend remote search, cascade invalidation, stale response rejection and pagination.
replace_once(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    'import { useEffect, useMemo, useState } from "react";\n',
    'import { useEffect, useMemo, useRef, useState } from "react";\n',
)
replace_once(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''type OptionLoadState = {
  status: string;
  message?: string;
  httpStatus?: number;
};
''',
    '''type OptionLoadState = {
  status: string;
  message?: string;
  httpStatus?: number;
  searchSupported?: boolean;
  dependsOn?: string[];
  missingDependencies?: string[];
  minQueryLength?: number;
  nextCursor?: string | number | null;
  hasMore?: boolean;
  total?: number | null;
  query?: string;
};
''',
)
replace_once(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''  const [optionState, setOptionState] = useState<Record<string, OptionLoadState>>({});

  const props = useMemo(() => skill?.parameters?.properties || {}, [skill]);
''',
    '''  const [optionState, setOptionState] = useState<Record<string, OptionLoadState>>({});
  const optionSearchTimers = useRef<Record<string, number>>({});
  const optionRequestSeq = useRef<Record<string, number>>({});

  const props = useMemo(() => skill?.parameters?.properties || {}, [skill]);
''',
)
replace_once(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''  const setVal = (k: string, v: unknown) => setValues((p) => ({ ...p, [k]: v }));

  async function loadOptions(key: string, p: JSONSchemaProperty, force = false) {
    if (!skill || optionLoading[key]) return;
    const dynamic = !!p?.["x-options-source"];
    if (!dynamic) {
      if (!(key in optionCache)) setOptionCache((prev) => ({ ...prev, [key]: normalizeOptions(p) }));
      return;
    }
    if (!force && OPTION_READY.has(optionState[key]?.status || "")) return;

    setOptionLoading((prev) => ({ ...prev, [key]: true }));
    setOptionState((prev) => ({ ...prev, [key]: { status: "loading" } }));
    try {
      const res = await listSkillOptions(skill.name, key);
      const status = res.source_status || ((res.options || []).length ? "ok" : "empty");
      if (OPTION_READY.has(status)) {
        setOptionCache((prev) => ({ ...prev, [key]: res.options || [] }));
        setOptionState((prev) => ({
          ...prev,
          [key]: { status, message: res.note, httpStatus: res.http_status },
        }));
        if (status === "empty" && res.note) message.info(res.note);
      } else {
        // 动态来源失败时不使用录制快照，也不保留之前选中的旧值。
        setOptionCache((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
        setVal(key, undefined);
        const detail = res.note || `候选来源不可用（${status}）`;
        setOptionState((prev) => ({
          ...prev,
          [key]: { status, message: detail, httpStatus: res.http_status },
        }));
        message.error(`${key}：${detail}`);
      }
    } catch (e: any) {
      setOptionCache((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      setVal(key, undefined);
      const detail = e?.response?.data?.detail || e.message || "候选来源请求失败";
      setOptionState((prev) => ({ ...prev, [key]: { status: "network_error", message: detail } }));
      message.error(`拉取 ${key} 候选失败：${detail}`);
    } finally {
      setOptionLoading((prev) => ({ ...prev, [key]: false }));
    }
  }
''',
    '''  const setVal = (k: string, v: unknown) => {
    const dependents = Object.entries(optionState)
      .filter(([, state]) => state.dependsOn?.includes(k))
      .map(([field]) => field);
    setValues((prev) => {
      const next = { ...prev, [k]: v };
      for (const field of dependents) delete next[field];
      return next;
    });
    if (dependents.length) {
      setOptionCache((prev) => {
        const next = { ...prev };
        for (const field of dependents) delete next[field];
        return next;
      });
      setOptionState((prev) => {
        const next = { ...prev };
        for (const field of dependents) {
          next[field] = { ...prev[field], status: "idle", message: undefined, nextCursor: null, hasMore: false };
        }
        return next;
      });
    }
  };

  async function loadOptions(
    key: string,
    p: JSONSchemaProperty,
    force = false,
    query = "",
    cursor?: string | number | null,
  ) {
    if (!skill) return;
    const dynamic = !!p?.["x-options-source"];
    if (!dynamic) {
      if (!(key in optionCache)) setOptionCache((prev) => ({ ...prev, [key]: normalizeOptions(p) }));
      return;
    }
    const append = cursor !== undefined && cursor !== null;
    if (append && optionLoading[key]) return;
    const state = optionState[key];
    if (!force && !append && !query && OPTION_READY.has(state?.status || "") && !state?.searchSupported) return;

    const seq = (optionRequestSeq.current[key] || 0) + 1;
    optionRequestSeq.current[key] = seq;
    setOptionLoading((prev) => ({ ...prev, [key]: true }));
    setOptionState((prev) => ({ ...prev, [key]: { ...prev[key], status: "loading", query } }));
    try {
      const res = await listSkillOptions(skill.name, key, {
        query: query || undefined,
        cursor: cursor ?? undefined,
        limit: 50,
        context: values,
      });
      if (optionRequestSeq.current[key] !== seq) return;
      const status = res.source_status || ((res.options || []).length ? "ok" : "empty");
      const capability = {
        searchSupported: !!res.search_supported,
        dependsOn: res.depends_on || [],
        missingDependencies: res.missing_dependencies,
        minQueryLength: res.min_query_length,
        nextCursor: res.next_cursor,
        hasMore: !!res.has_more,
        total: res.total,
        query,
      };
      if (OPTION_READY.has(status)) {
        setOptionCache((prev) => {
          const incoming = res.options || [];
          if (!append) return { ...prev, [key]: incoming };
          const merged = [...(prev[key] || []), ...incoming];
          const seen = new Set<string>();
          return {
            ...prev,
            [key]: merged.filter((item) => {
              const id = `${item.label}\\u0000${String(item.value)}`;
              if (seen.has(id)) return false;
              seen.add(id);
              return true;
            }),
          };
        });
        setOptionState((prev) => ({
          ...prev,
          [key]: { status, message: res.note, httpStatus: res.http_status, ...capability },
        }));
        if (status === "empty" && res.note && !query) message.info(res.note);
      } else {
        setOptionCache((prev) => {
          const next = { ...prev };
          if (!append) delete next[key];
          return next;
        });
        setValues((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
        const detail = res.note || `候选来源不可用（${status}）`;
        setOptionState((prev) => ({
          ...prev,
          [key]: { status, message: detail, httpStatus: res.http_status, ...capability },
        }));
        if (!["missing_dependency", "query_required", "query_too_short"].includes(status)) {
          message.error(`${key}：${detail}`);
        }
      }
    } catch (e: any) {
      if (optionRequestSeq.current[key] !== seq) return;
      setOptionCache((prev) => {
        const next = { ...prev };
        if (!append) delete next[key];
        return next;
      });
      setValues((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      const detail = e?.response?.data?.detail || e.message || "候选来源请求失败";
      setOptionState((prev) => ({ ...prev, [key]: { ...prev[key], status: "network_error", message: detail } }));
      message.error(`拉取 ${key} 候选失败：${detail}`);
    } finally {
      if (optionRequestSeq.current[key] === seq) {
        setOptionLoading((prev) => ({ ...prev, [key]: false }));
      }
    }
  }

  function scheduleOptionSearch(key: string, p: JSONSchemaProperty, query: string) {
    if (optionSearchTimers.current[key]) window.clearTimeout(optionSearchTimers.current[key]);
    optionSearchTimers.current[key] = window.setTimeout(() => {
      loadOptions(key, p, true, query);
    }, 250);
  }
''',
)
replace_once(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''      const state = optionState[key];
      const sourceFailed = dynamic && isOptionSourceFailure(state);
      const options = dynamic ? (optionCache[key] || []) : normalizeOptions(p);
''',
    '''      const state = optionState[key];
      const sourceFailed = dynamic && isOptionSourceFailure(state);
      const sourceWaiting = dynamic && ["missing_dependency", "query_required", "query_too_short"].includes(state?.status || "");
      const options = dynamic ? (optionCache[key] || []) : normalizeOptions(p);
''',
)
replace_once(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''          showSearch
          allowClear
          optionFilterProp="label"
''',
    '''          showSearch
          allowClear
          optionFilterProp="label"
          filterOption={state?.searchSupported ? false : undefined}
''',
)
replace_once(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''              : sourceFailed ? "候选来源不可用"
                : state?.status === "empty" ? "当前条件下没有可选项"
                  : dynamic ? "打开下拉加载实时候选" : "无可选项"
          }
          onFocus={() => loadOptions(key, p)}
          onDropdownVisibleChange={(open) => { if (open) loadOptions(key, p); }}
          onChange={(v) => setVal(key, v)}
''',
    '''              : sourceWaiting ? (state?.message || "请先补充查询条件")
                : sourceFailed ? "候选来源不可用"
                  : state?.status === "empty" ? "当前条件下没有可选项"
                    : dynamic ? "打开下拉加载实时候选" : "无可选项"
          }
          onFocus={() => loadOptions(key, p)}
          onDropdownVisibleChange={(open) => { if (open) loadOptions(key, p); }}
          onSearch={(text) => { if (state?.searchSupported) scheduleOptionSearch(key, p, text); }}
          dropdownRender={(menu) => (
            <>
              {menu}
              {state?.hasMore && (
                <div style={{ borderTop: "1px solid #f0f0f0", padding: 6, textAlign: "center" }}>
                  <Button
                    type="link"
                    size="small"
                    loading={!!optionLoading[key]}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => loadOptions(key, p, true, state.query || "", state.nextCursor)}
                  >
                    加载更多{state.total != null ? `（共 ${state.total} 项）` : ""}
                  </Button>
                </div>
              )}
            </>
          )}
          onChange={(v) => setVal(key, v)}
''',
)
replace_once(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''      if (sourceFailed) {
        sourceHint = (
          <Alert
            style={{ marginTop: 6 }}
            type="error"
            showIcon
            message={state?.message || "动态候选来源不可用"}
            description={state?.httpStatus ? `HTTP ${state.httpStatus}` : undefined}
            action={<Button size="small" onClick={() => loadOptions(key, p, true)}>重试</Button>}
          />
        );
      } else if (dynamic && state?.status === "empty") {
''',
    '''      if (sourceWaiting) {
        sourceHint = (
          <Alert
            style={{ marginTop: 6 }}
            type="warning"
            showIcon
            message={state?.message || "请先补充候选查询条件"}
            description={state?.missingDependencies?.length ? `依赖字段：${state.missingDependencies.join("、")}` : undefined}
          />
        );
      } else if (sourceFailed) {
        sourceHint = (
          <Alert
            style={{ marginTop: 6 }}
            type="error"
            showIcon
            message={state?.message || "动态候选来源不可用"}
            description={state?.httpStatus ? `HTTP ${state.httpStatus}` : undefined}
            action={<Button size="small" onClick={() => loadOptions(key, p, true, state?.query || "")}>重试</Button>}
          />
        );
      } else if (dynamic && state?.status === "empty") {
''',
)

print("Option Query P1 contract codemod applied")
