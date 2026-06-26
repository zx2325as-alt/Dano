from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: pattern count={text.count(old)}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


replace(
    "skillfrontend/src/api/skills.ts",
    '''  "x-option-label"?: string;
  "x-option-value"?: string;
}
''',
    '''  "x-option-label"?: string;
  "x-option-value"?: string;
  "x-options-search"?: boolean;
  "x-options-min-query-length"?: number;
  "x-options-depends-on"?: string[];
  "x-options-pagination"?: "page" | "offset" | "cursor" | string;
  "x-options-validation"?: boolean;
}
''',
)
replace(
    "skillfrontend/src/api/skills.ts",
    '''  | "response_too_large"
  | "source_error";
''',
    '''  | "response_too_large"
  | "missing_dependency"
  | "query_required"
  | "query_too_short"
  | "query_too_long"
  | "invalid_cursor"
  | "invalid_context"
  | "invalid_query_protocol"
  | "validation_unsupported"
  | "source_error";
''',
)
replace(
    "skillfrontend/src/api/skills.ts",
    '''  search_supported?: boolean;
  depends_on?: string[];
''',
    '''  search_supported?: boolean;
  validation_supported?: boolean;
  depends_on?: string[];
''',
)
replace(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''    const dependents = Object.entries(optionState)
      .filter(([, state]) => state.dependsOn?.includes(k))
      .map(([field]) => field);
''',
    '''    const dependents = Object.keys(props).filter((field) => {
      const declared = props[field]?.["x-options-depends-on"] || [];
      return declared.includes(k) || optionState[field]?.dependsOn?.includes(k);
    });
''',
)
replace(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''      const state = optionState[key];
      const sourceFailed = dynamic && isOptionSourceFailure(state);
      const sourceWaiting = dynamic && ["missing_dependency", "query_required", "query_too_short"].includes(state?.status || "");
      const options = dynamic ? (optionCache[key] || []) : normalizeOptions(p);
''',
    '''      const state = optionState[key];
      const sourceWaiting = dynamic && ["missing_dependency", "query_required", "query_too_short"].includes(state?.status || "");
      const sourceFailed = dynamic && !sourceWaiting && isOptionSourceFailure(state);
      const remoteSearch = !!p?.["x-options-search"] || !!state?.searchSupported;
      const options = dynamic ? (optionCache[key] || []) : normalizeOptions(p);
''',
)
replace(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''          filterOption={state?.searchSupported ? false : undefined}
''',
    '''          filterOption={remoteSearch ? false : undefined}
''',
)
replace(
    "skillfrontend/src/components/InvokeDrawer.tsx",
    '''          onSearch={(text) => { if (state?.searchSupported) scheduleOptionSearch(key, p, text); }}
''',
    '''          onSearch={(text) => { if (remoteSearch) scheduleOptionSearch(key, p, text); }}
''',
)
