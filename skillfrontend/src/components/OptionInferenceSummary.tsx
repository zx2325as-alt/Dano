import { Space, Tag, Tooltip, Typography } from "antd";

export interface OptionQueryProtocolView {
  search?: Record<string, unknown>;
  pagination?: { mode?: string } & Record<string, unknown>;
  validation?: Record<string, unknown>;
  dependencies?: Array<{ field?: string }>;
}

export interface OptionQueryInferenceView {
  status?: string;
  confidence?: number;
  confirmed_by_user?: boolean;
  evidence?: Array<{ kind?: string; evidence_refs?: string[]; reason?: string }>;
}

export interface OptionInferenceSelectView {
  option_query?: OptionQueryProtocolView;
  option_query_inference?: OptionQueryInferenceView;
}

function percent(value?: number): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "";
}

export default function OptionInferenceSummary({ select }: { select?: OptionInferenceSelectView | null }) {
  const protocol = select?.option_query;
  if (!protocol) return null;

  const inference = select?.option_query_inference;
  const evidenceCount = (inference?.evidence || []).reduce(
    (total, item) => total + (item.evidence_refs?.length || 0),
    0,
  );
  const dependencies = (protocol.dependencies || [])
    .map((item) => item.field)
    .filter((field): field is string => !!field);
  const mode = protocol.pagination?.mode;
  const inferred = inference?.status === "inferred";

  return (
    <Space size={[4, 4]} wrap>
      <Tooltip title={inferred
        ? `根据 ${evidenceCount} 条录制证据自动推断；发布前仍由确定性检查验证`
        : "该候选字段已配置查询能力"}>
        <Tag color={inferred ? "cyan" : "blue"} style={{ fontSize: 11 }}>
          {inferred ? `录制推断 ${percent(inference?.confidence)}` : "查询能力已配置"}
        </Tag>
      </Tooltip>
      {protocol.search && <Tag color="geekblue" style={{ fontSize: 11 }}>可搜索</Tag>}
      {mode && <Tag color="purple" style={{ fontSize: 11 }}>{mode} 分页</Tag>}
      {protocol.validation && <Tag color="green" style={{ fontSize: 11 }}>提交前精确核验</Tag>}
      {dependencies.length > 0 && (
        <Tooltip title={`先填写：${dependencies.join("、")}`}>
          <Tag color="gold" style={{ fontSize: 11 }}>级联依赖 {dependencies.length} 项</Tag>
        </Tooltip>
      )}
      {inferred && !inference?.confirmed_by_user && (
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          自动结果，可在发布前核对
        </Typography.Text>
      )}
    </Space>
  );
}
