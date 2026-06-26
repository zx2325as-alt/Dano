from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "skillfrontend/src/api/skills.ts"


def replace_between(text: str, start: str, end: str, replacement: str) -> str:
    left = text.find(start)
    if left < 0:
        raise RuntimeError(f"start marker not found: {start}")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"end marker not found: {end}")
    return text[:left] + replacement + text[right:]


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    text = text.replace("value: string | number;", "value: string | number | boolean;")

    statuses = '''export type OptionSourceStatus =
  | "ok"
  | "empty"
  | "not_dynamic"
  | "auth_expired"
  | "permission_denied"
  | "source_not_found"
  | "source_unavailable"
  | "source_conflict"
  | "rate_limited"
  | "network_error"
  | "invalid_request"
  | "invalid_response"
  | "invalid_shape"
  | "invalid_mapping"
  | "invalid_source_url"
  | "invalid_base_url"
  | "invalid_cursor"
  | "invalid_context"
  | "invalid_binding"
  | "needs_context"
  | "credential_in_url"
  | "sensitive_request"
  | "cross_origin_blocked"
  | "unsafe_method"
  | "unsupported_method"
  | "mixed_candidate_shape"
  | "ambiguous_records"
  | "ambiguous_values"
  | "ambiguous_labels"
  | "too_many_options"
  | "response_too_large"
  | "source_error";
'''
    text = replace_between(
        text,
        "export type OptionSourceStatus =\n",
        "\n\nexport interface ToolOptionsResponse {\n",
        statuses,
    )

    response = '''export interface ToolOptionsResponse {
  field: string;
  count: number;
  count_exact?: boolean;
  options: ToolOption[];
  submit_mode?: string;
  source_status?: OptionSourceStatus | string;
  http_status?: number;
  note?: string;
  protocol_version?: string;
  returned?: number;
  has_more?: boolean;
  next_cursor?: string | null;
  dependencies?: string[];
  truncated?: boolean;
  deduplicated_count?: number;
  invalid_item_count?: number;
  conflict_count?: number;
}
'''
    text = replace_between(
        text,
        "export interface ToolOptionsResponse {\n",
        "\n\nexport interface ToolOptionsQuery {\n",
        response,
    )
    PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
