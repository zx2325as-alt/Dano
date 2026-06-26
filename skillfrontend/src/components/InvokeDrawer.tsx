import { useEffect, useMemo, useRef, useState } from "react";
import {
  Drawer, Button, Checkbox, Input, InputNumber, DatePicker, Radio, Select, Typography, Tag,
  Alert, Descriptions, Space, message, Image,
} from "antd";
import {
  invokeSkill, listSkillOptions, SkillManifest, TaskOutcome, JSONSchema, JSONSchemaProperty, ToolOption,
} from "../api/skills";

const STATE_COLOR: Record<string, string> = {
  completed: "success", failed: "error", rejected: "error",
  cancelled: "warning", needs_input: "warning", needs_select: "warning",
};

const OPTION_READY = new Set(["ok", "empty"]);
const OPTION_NON_ERROR = new Set(["idle", "loading", "ok", "empty", "needs_context"]);

type OptionLoadState = {
  status: string;
  message?: string;
  httpStatus?: number;
};

// 候选项标识:优先 id,否则第一个值(与后端 _candidate_id 一致)
function candidateId(c: Record<string, unknown>): unknown {
  return c.id ?? Object.values(c)[0];
}
function candidateLabel(c: Record<string, unknown>, tmpl?: string): string {
  if (tmpl) return tmpl.replace(/\{(\w+)\}/g, (_, k) => String(c[k] ?? ""));
  return JSON.stringify(c);
}

// 按字段名/描述猜控件类型(manifest 目前统一 type=string,故靠语义猜:日期/数字/文本)
const isDate = (s: string) => /date|time|日期|时间|起止|开始|结束|起|止/i.test(s);
const isNum = (s: string) => /days|num|count|amount|qty|天数|数量|金额|时长|个数/i.test(s);
const isSelectProp = (p: JSONSchemaProperty) =>
  p?.format === "name-ref" || p?.["x-options-source"] || Array.isArray(p?.["x-options"]) || Array.isArray(p?.enum);
const isMultiSelectProp = (p: JSONSchemaProperty) => p?.type === "array" || p?.format === "name-ref-list";

function normalizeOptions(p: JSONSchemaProperty): ToolOption[] {
  const raw = (Array.isArray(p?.["x-options"]) && p["x-options"]!.length ? p["x-options"] : p?.enum) || [];
  const out: ToolOption[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const rec = item as Record<string, unknown>;
    const label = typeof item === "object" && item !== null && "label" in rec ? String(rec.label ?? "") : String(item ?? "");
    const rawValue = typeof item === "object" && item !== null && "value" in rec ? rec.value : item;
    const value = typeof rawValue === "number" ? rawValue : String(rawValue ?? "");
    if (!label) continue;
    const key = `${label}\u0000${String(value)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ label, value });
  }
  return out;
}

function jsonSkeleton(p: JSONSchema): string {
  const o: Record<string, unknown> = {};
  for (const k of Object.keys(p?.properties || {})) o[k] = "";
  return JSON.stringify(o, null, 2);
}

function isOptionSourceFailure(state?: OptionLoadState): boolean {
  return !!state && !OPTION_NON_ERROR.has(state.status);
}

export default function InvokeDrawer({ skill, onClose }: { skill: SkillManifest | null; onClose: () => void }) {
  const [mode, setMode] = useState<"form" | "json">("form");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [text, setText] = useState("{}");
  const [confirm, setConfirm] = useState(false);
  const [running, setRunning] = useState(false);
  const [out, setOut] = useState<TaskOutcome | null>(null);
  const [lastInput, setLastInput] = useState<Record<string, unknown>>({});  // 供消歧选中后带同一组输入重调
  const [optionCache, setOptionCache] = useState<Record<string, ToolOption[]>>({});
  const [optionLoading, setOptionLoading] = useState<Record<string, boolean>>({});
  const [optionState, setOptionState] = useState<Record<string, OptionLoadState>>({});
  const [optionCursor, setOptionCursor] = useState<Record<string, string | null>>({});
  const [optionHasMore, setOptionHasMore] = useState<Record<string, boolean>>({});
  const [optionQuery, setOptionQuery] = useState<Record<string, string>>({});
  const optionTimers = useRef<Record<string, number>>({});
  const optionRequestSeq = useRef<Record<string, number>>({});

  const props = useMemo(() => skill?.parameters?.properties || {}, [skill]);
  const required = useMemo(() => new Set(skill?.parameters?.required || []), [skill]);

  useEffect(() => {
    if (skill) {
      setValues({});
      setText(jsonSkeleton(skill.parameters));
      setConfirm(skill.requires_confirmation);
      setMode("form");
      setOut(null);
      setOptionCache({});
      setOptionLoading({});
      setOptionState({});
      setOptionCursor({});
      setOptionHasMore({});
      setOptionQuery({});
      Object.values(optionTimers.current).forEach((timer) => window.clearTimeout(timer));
      optionTimers.current = {};
      optionRequestSeq.current = {};
    }
  }, [skill]);

  const clearOptionFields = (fields: string[], clearValues = true) => {
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

  const dependentOptionFields = (changed: string): string[] => {
    const result = new Set<string>();
    const queue = [changed];
    while (queue.length) {
      const current = queue.shift() as string;
      for (const [field, prop] of Object.entries(props)) {
        if (field === changed || result.has(field)) continue;
        if ((prop?.["x-option-depends-on"] || []).includes(current)) {
          result.add(field);
          queue.push(field);
        }
      }
    }
    return [...result];
  };

  const setVal = (key: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    clearOptionFields(dependentOptionFields(key));
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
    if (!skill) return;
    if (optionLoading[key] && !args.force) return;
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

  async function doInvoke(input: Record<string, unknown>) {
    if (!skill) return;
    setRunning(true);
    setOut(null);
    setLastInput(input);
    try {
      setOut(await invokeSkill(skill.name, input, confirm));
    } catch (e: any) {
      message.error("调用失败:" + (e?.response?.data?.detail || e.message));
    } finally {
      setRunning(false);
    }
  }

  async function run() {
    if (!skill) return;
    let input: Record<string, unknown>;
    if (mode === "json") {
      try {
        input = JSON.parse(text || "{}");
      } catch (e: any) {
        message.error("输入不是合法 JSON:" + e.message);
        return;
      }
    } else {
      const missing = [...required].filter((k) => values[k] === "" || values[k] == null);
      if (missing.length) {
        message.error("缺必填:" + missing.join(", "));
        return;
      }
      const brokenSources = Object.keys(props).filter((k) => {
        if (!props[k]?.["x-options-source"]) return false;
        if (!required.has(k) && (values[k] === "" || values[k] == null)) return false;
        return isOptionSourceFailure(optionState[k]);
      });
      if (brokenSources.length) {
        message.error("以下动态候选来源不可用，不能提交：" + brokenSources.join(", "));
        return;
      }
      // 丢掉空的可选字段;数字/日期已是正确类型
      input = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "" && v != null));
    }
    await doInvoke(input);
  }

  const fieldRow = (key: string, p: JSONSchemaProperty) => {
    const label = p.label || p.description || key;
    const hint = `${key} ${label}`;
    const reqMark = required.has(key) ? <span style={{ color: "#cf1322" }}> *</span> : null;
    let widget;
    let sourceHint = null;
    if (isSelectProp(p)) {
      const dynamic = !!p?.["x-options-source"];
      const state = optionState[key];
      const sourceFailed = dynamic && isOptionSourceFailure(state);
      const options = dynamic ? (optionCache[key] || []) : normalizeOptions(p);
      const multi = isMultiSelectProp(p);
      widget = (
        <Select
          mode={multi ? "multiple" : undefined}
          showSearch
          allowClear
          optionFilterProp="label"
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
        />
      );
      if (sourceFailed) {
        sourceHint = (
          <Alert
            style={{ marginTop: 6 }}
            type="error"
            showIcon
            message={state?.message || "动态候选来源不可用"}
            description={state?.httpStatus ? `HTTP ${state.httpStatus}` : undefined}
            action={<Button size="small" onClick={() => loadOptions(key, p, { force: true })}>重试</Button>}
          />
        );
      } else if (dynamic && state?.status === "needs_context") {
        sourceHint = (
          <Alert style={{ marginTop: 6 }} type="info" showIcon message={state.message || "请先填写依赖字段"} />
        );
      } else if (dynamic && state?.status === "empty") {
        sourceHint = (
          <Space size={4} style={{ marginTop: 4 }}>
            <Typography.Text type="secondary">{state.message || "当前条件下没有可选项"}</Typography.Text>
            {optionHasMore[key] ? (
              <Button type="link" size="small" loading={!!optionLoading[key]}
                      onClick={() => loadMoreOptions(key, p)}>继续查找</Button>
            ) : (
              <Button type="link" size="small" onClick={() => loadOptions(key, p, { force: true })}>重新加载</Button>
            )}
          </Space>
        );
      } else if (dynamic && optionHasMore[key]) {
        sourceHint = (
          <Button type="link" size="small" loading={!!optionLoading[key]}
                  onClick={() => loadMoreOptions(key, p)}>加载更多候选</Button>
        );
      }
    } else if (isDate(hint)) {
      widget = <DatePicker style={{ width: "100%" }} onChange={(_, ds) => setVal(key, ds)} />;
    } else if (isNum(hint)) {
      widget = <InputNumber style={{ width: "100%" }} value={values[key] as number}
                            onChange={(v) => setVal(key, v)} />;
    } else {
      widget = <Input value={(values[key] as string) ?? ""} onChange={(e) => setVal(key, e.target.value)}
                      placeholder={key} />;
    }
    return (
      <div key={key} style={{ marginBottom: 12 }}>
        <div style={{ marginBottom: 4, fontSize: 13 }}>
          {label}{reqMark}{label !== key && <Typography.Text type="secondary" style={{ fontSize: 12 }}> · {key}</Typography.Text>}
        </div>
        {widget}
        {sourceHint}
      </div>
    );
  };

  const keys = Object.keys(props);

  return (
    <Drawer title={skill ? `测试调用 · ${skill.name}` : ""} width={560} open={!!skill} onClose={onClose} destroyOnClose>
      {skill && (
        <>
          <Descriptions size="small" column={1} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="风险">
              <Tag color={skill.risk_level >= "L3" ? "orange" : "default"}>{skill.risk_level}</Tag>
              {skill.requires_confirmation && <Tag color="orange">写操作需确认</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="必填字段">{[...required].length ? [...required].join(", ") : "无"}</Descriptions.Item>
          </Descriptions>

          <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)} size="small" style={{ marginBottom: 12 }}>
            <Radio.Button value="form">逐字段填写</Radio.Button>
            <Radio.Button value="json">原始 JSON</Radio.Button>
          </Radio.Group>

          {mode === "form" ? (
            <>
              <Typography.Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
                业务字段(__base_url__ / 模板 / 凭证后端注入,无需填)
              </Typography.Text>
              {keys.length ? keys.map((k) => fieldRow(k, props[k])) : <Typography.Text type="secondary">该 skill 无参数</Typography.Text>}
            </>
          ) : (
            <>
              <Typography.Text type="secondary">input(业务字段 JSON)</Typography.Text>
              <Input.TextArea value={text} onChange={(e) => setText(e.target.value)} autoSize={{ minRows: 8, maxRows: 18 }}
                              style={{ fontFamily: "monospace", marginTop: 6 }} />
            </>
          )}

          <Space style={{ marginTop: 12 }}>
            <Checkbox checked={confirm} onChange={(e) => setConfirm(e.target.checked)}>confirm(L3 写操作必须勾)</Checkbox>
            <Button type="primary" loading={running} onClick={run}>调用</Button>
          </Space>

          {out && (
            <div style={{ marginTop: 16 }}>
              <Alert
                type={(STATE_COLOR[out.state] as any) || "info"}
                showIcon
                message={<span>state: <b>{out.state}</b></span>}
                description={out.message}
              />
              {out.state === "needs_select" && (() => {
                const sel = ((out.audit as any)?.select || {}) as { bind?: string; candidates?: Record<string, unknown>[]; label_template?: string };
                const cands = sel.candidates || [];
                return (
                  <div style={{ marginTop: 12 }}>
                    <Typography.Text>请选择一个候选(将以 <b>{sel.bind}</b> 带同一组输入重新调用):</Typography.Text>
                    <Space wrap style={{ marginTop: 8 }}>
                      {cands.map((c, i) => (
                        <Button key={i} loading={running}
                                onClick={() => doInvoke({ ...lastInput, [sel.bind as string]: candidateId(c) })}>
                          {candidateLabel(c, sel.label_template)}
                        </Button>
                      ))}
                    </Space>
                  </div>
                );
              })()}
              <Typography.Text type="secondary" style={{ display: "block", marginTop: 12 }}>返回(structured_output)</Typography.Text>
              <Input.TextArea
                readOnly
                value={JSON.stringify(out.exec_result?.structured_output ?? null, null, 2)}
                autoSize={{ minRows: 4, maxRows: 14 }}
                style={{ fontFamily: "monospace", marginTop: 6 }}
              />
              {(() => {
                const shots = (((out.exec_result as any)?.evidence?.screenshots) || []) as string[];
                const imgs = shots.filter((s) => typeof s === "string" && s.startsWith("data:image"));
                if (!imgs.length) return null;
                return (
                  <div style={{ marginTop: 12 }}>
                    <Typography.Text type="secondary" style={{ display: "block", marginBottom: 6 }}>
                      页面执行截图({imgs.length})· 点击放大
                    </Typography.Text>
                    <Image.PreviewGroup>
                      <Space wrap>
                        {imgs.map((src, i) => (
                          <Image key={i} src={src} width={130}
                                 style={{ border: "1px solid #f0f0f0", borderRadius: 4 }} />
                        ))}
                      </Space>
                    </Image.PreviewGroup>
                  </div>
                );
              })()}
              {out.audit && (out.audit as any).fact_check && (
                <Alert style={{ marginTop: 10 }} type="info" message="事实核查证据" description={<pre style={{ margin: 0, fontSize: 12 }}>{JSON.stringify((out.audit as any).fact_check, null, 2)}</pre>} />
              )}
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}
