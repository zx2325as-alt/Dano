import { Radio, Space, Tag, Tooltip, Typography } from "antd";

export interface OptionQueryProtocolView {
  search?: Record<string, unknown>;
  pagination?: { mode?: string } & Record<string, unknown>;
  validation?: Record<string, unknown>;
  dependencies?: Array<{ field?: string }>;
}

export interface OptionCapabilitiesView {
  search?: boolean;
  pagination?: string;
  validation?: boolean;
  dependencies?: string[];
}

export interface OptionQueryInferenceView {
  status?: string;
  confidence?: number;
  confirmed_by_user?: boolean;
  evidence?: Array<{ kind?: string; evidence_refs?: string[]; reason?: string }>;
  evidence_count?: number;
  review_id?: string;
}

export interface OptionInferenceSelectView {
  capabilities?: OptionCapabilitiesView;
  inference?: OptionQueryInferenceView;
  option_query?: OptionQueryProtocolView;
  option_query_inference?: OptionQueryInferenceView;
}

export type OptionReviewDecision = "accept" | "reject";

function percent(value?: number): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "";
}

function capabilityView(select?: OptionInferenceSelectView | null): OptionCapabilitiesView | null {
  if (select?.capabilities) return select.capabilities;
  const protocol = select?.option_query;
  if (!protocol) return null;
  return {
    search: !!protocol.search,
    pagination: protocol.pagination?.mode || "",
    validation: !!protocol.validation,
    dependencies: (protocol.dependencies || [])
      .map((item) => item.field)
      .filter((field): field is string => !!field),
  };
}

export default function OptionInferenceSummary({
  select,
  decision,
  onDecision,
}: {
  select?: OptionInferenceSelectView | null;
  decision?: OptionReviewDecision;
  onDecision?: (decision: OptionReviewDecision) => void;
}) {
  const capabilities = capabilityView(select);
  if (!capabilities) return null;

  const inference = select?.inference || select?.option_query_inference;
  const evidenceCount = inference?.evidence_count ?? (inference?.evidence || []).reduce(
    (total, item) => total + (item.evidence_refs?.length || 0),
    0,
  );
  const dependencies = capabilities.dependencies || [];
  const inferred = inference?.status === "inferred";
  const pendingReview = inferred && !!inference?.review_id && !inference.confirmed_by_user;

  return (
    <Space size={[4, 4]} wrap>
      <Tooltip title={inferred
        ? `根据 ${evidenceCount} 条录制证据自动推断；确认只决定是否采用，内部请求配置不能由浏览器修改`
        : "该候选字段已配置查询能力"}>
        <Tag color={inferred ? "cyan" : "blue"} style={{ fontSize: 11 }}>
          {inferred ? `录制推断 ${percent(inference?.confidence)}` : "查询能力已配置"}
        </Tag>
      </Tooltip>
      {capabilities.search && <Tag color="geekblue" style={{ fontSize: 11 }}>可搜索</Tag>}
      {capabilities.pagination && <Tag color="purple" style={{ fontSize: 11 }}>{capabilities.pagination} 分页</Tag>}
      {capabilities.validation && <Tag color="green" style={{ fontSize: 11 }}>提交前精确核验</Tag>}
      {dependencies.length > 0 && (
        <Tooltip title={`先填写：${dependencies.join("、")}`}>
          <Tag color="gold" style={{ fontSize: 11 }}>级联依赖 {dependencies.length} 项</Tag>
        </Tooltip>
      )}
      {pendingReview && onDecision && (
        <Radio.Group
          size="small"
          value={decision}
          onChange={(event) => onDecision(event.target.value as OptionReviewDecision)}
          optionType="button"
          buttonStyle="solid"
        >
          <Radio.Button value="accept">确认正确</Radio.Button>
          <Radio.Button value="reject">不采用</Radio.Button>
        </Radio.Group>
      )}
      {pendingReview && !decision && (
        <Typography.Text type="warning" style={{ fontSize: 11 }}>
          发布前需确认
        </Typography.Text>
      )}
    </Space>
  );
}
