from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, new: str, label: str) -> str:
    left = text.find(start)
    if left < 0:
        raise RuntimeError(f"{label}: start not found")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"{label}: end not found")
    return text[:left] + new + text[right:]


def patch_option_p0() -> None:
    path = "back/dano/execution/page/option_p0.py"
    text = read(path)
    text = replace_once(
        text,
        '    "source_records_path",\n    "primitive",\n',
        '    "source_records_path",\n    "source_input_bindings",\n    "primitive",\n',
        "compiled binding metadata",
    )
    text = replace_once(
        text,
        '''    kwargs: dict = {}
    if method == "GET":
        if spec["query"]:
            kwargs["params"] = spec["query"]
    else:
''',
        '''    kwargs: dict = {}
    if spec["query"]:
        kwargs["params"] = spec["query"]
    if method != "GET":
''',
        "query params for all methods",
    )

    start = 'def _apply_read_metadata(select: dict, read: dict) -> None:\n'
    end = '\n\ndef _enrich_select_sources(original):\n'
    block = '''_SEARCH_BINDING_KEYS = {"q", "query", "keyword", "keywords", "search", "searchtext", "term", "filtertext"}
_PAGE_BINDING_KEYS = {"page", "pageno", "pagenum", "pageindex", "current", "currentpage"}
_LIMIT_BINDING_KEYS = {"limit", "pagesize", "perpage", "pagecount"}
_OFFSET_BINDING_KEYS = {"offset", "start", "startindex", "skip"}


def _source_key(key: str) -> str:
    return str(key or "").lower().replace("_", "").replace("-", "")


def _source_value_type(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _flatten_source_values(node, tokens: list | None = None) -> list[tuple[list, object]]:
    tokens = list(tokens or [])
    out: list[tuple[list, object]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            out.extend(_flatten_source_values(value, tokens + [str(key)]))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.extend(_flatten_source_values(value, tokens + [index]))
    else:
        out.append((tokens, node))
    return out


def _read_body_object(read: dict):
    raw = read.get("post_data")
    content_type = str(read.get("content_type") or "").lower()
    if isinstance(raw, (dict, list)):
        return copy.deepcopy(raw)
    if raw in (None, ""):
        return None
    if "form-urlencoded" in content_type:
        return dict(parse_qsl(str(raw), keep_blank_values=True))
    try:
        return json.loads(str(raw))
    except Exception:  # noqa: BLE001
        return None


def _sample_labels(samples: dict | None) -> dict[str, str]:
    by_value: dict[str, list[str]] = {}
    for label, value in (samples or {}).items():
        if value in (None, "") or isinstance(value, (dict, list)):
            continue
        by_value.setdefault(str(value), []).append(str(label))
    return {value: labels[0] for value, labels in by_value.items() if len(set(labels)) == 1}


def _infer_source_input_bindings(read: dict, samples: dict | None) -> list[dict]:
    """Infer only high-confidence search, pagination and exact context bindings."""
    bindings: list[dict] = []
    labels = _sample_labels(samples)
    parsed = urlparse(str(read.get("url") or ""))
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    candidates: list[tuple[str, list, object]] = [
        ("query", [key], value) for key, value in query.items()
    ]
    body = _read_body_object(read)
    if isinstance(body, (dict, list)):
        candidates.extend(("body", tokens, value) for tokens, value in _flatten_source_values(body))

    for target, tokens, value in candidates:
        if not tokens or isinstance(tokens[-1], int):
            continue
        key = _source_key(str(tokens[-1]))
        binding: dict | None = None
        if key in _SEARCH_BINDING_KEYS:
            binding = {"from": "query", "target": target, "tokens": tokens, "value_type": "string"}
        elif key in _LIMIT_BINDING_KEYS:
            binding = {"from": "limit", "target": target, "tokens": tokens, "value_type": "integer"}
        elif key in _OFFSET_BINDING_KEYS:
            binding = {"from": "offset", "target": target, "tokens": tokens, "value_type": "integer"}
        elif key in _PAGE_BINDING_KEYS and str(value).lstrip("-").isdigit():
            recorded = int(value)
            base = recorded if recorded in (0, 1) else 1
            binding = {"from": "page", "target": target, "tokens": tokens,
                       "value_type": "integer", "page_base": base}
        else:
            label = labels.get(str(value))
            if label:
                binding = {"from": f"context.{label}", "target": target, "tokens": tokens,
                           "value_type": _source_value_type(value), "required": True}
        if binding and binding not in bindings:
            bindings.append(binding)
    return bindings


def _apply_read_metadata(select: dict, read: dict, samples: dict | None = None) -> None:
    select["source_method"] = str(read.get("method") or "GET").upper()
    raw_url = str(read.get("url") or select.get("source_url") or "")
    parsed = urlparse(raw_url)
    clean_url = parsed._replace(query="", fragment="").geturl()
    select["source_url"] = clean_url or raw_url
    source_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if source_query:
        select["source_query"] = source_query
    if read.get("post_data") not in (None, ""):
        select["source_post_data"] = read.get("post_data")
    if read.get("content_type"):
        select["source_content_type"] = read.get("content_type")
    if read.get("source_headers"):
        select["source_headers"] = copy.deepcopy(read.get("source_headers"))
    if "records_path" in read:
        select["source_records_path"] = copy.deepcopy(read.get("records_path"))
    else:
        records_path = _find_list_path(read.get("json"))
        if records_path is not None:
            select["source_records_path"] = records_path
    bindings = _infer_source_input_bindings(read, samples)
    if bindings:
        select["source_input_bindings"] = bindings
'''
    text = replace_between(text, start, end, block, "source binding inference")
    text = replace_once(
        text,
        '                _apply_read_metadata(select, matches[-1])\n',
        '                _apply_read_metadata(select, matches[-1], samples)\n',
        "inference samples",
    )
    write(path, text)


def patch_option_query() -> None:
    path = "back/dano/execution/page/option_query.py"
    text = read(path)
    text = replace_once(
        text,
        '''    if source == "offset":
        return offset
    if source == "const":
''',
        '''    if source == "offset":
        return offset
    if source == "page":
        return int(binding.get("page_base", 1)) + (offset // max(limit, 1))
    if source == "const":
''',
        "page binding value",
    )
    text = replace_once(
        text,
        '        if source not in {"query", "limit", "offset", "const"} and not source.startswith("context."):\n',
        '        if source not in {"query", "limit", "offset", "page", "const"} and not source.startswith("context."):\n',
        "page binding allowlist",
    )
    text = replace_once(
        text,
        '        if source in {"limit", "offset"}:\n            used_pagination = True\n',
        '        if source in {"limit", "offset", "page"}:\n            used_pagination = True\n',
        "page pagination flag",
    )
    text = replace_once(
        text,
        '''        if len(collected) >= limit or last_raw_count < limit or last_raw_count == 0:
            break
''',
        '''        # Do not consume a second upstream page once this response has matches:
        # advancing past a partly-used page would silently drop candidates. Empty pages
        # may be skipped (bounded) so local filtering can still find a visible result.
        if collected or last_raw_count < limit or last_raw_count == 0:
            break
''',
        "lossless upstream page stop",
    )
    write(path, text)


def patch_frontend() -> None:
    path = "skillfrontend/src/components/InvokeDrawer.tsx"
    text = read(path)
    old = '''      } else if (dynamic && state?.status === "empty") {
        sourceHint = (
          <Space size={4} style={{ marginTop: 4 }}>
            <Typography.Text type="secondary">{state.message || "当前条件下没有可选项"}</Typography.Text>
            <Button type="link" size="small" onClick={() => loadOptions(key, p, { force: true })}>重新加载</Button>
          </Space>
        );
      }
'''
    new = '''      } else if (dynamic && state?.status === "empty") {
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
'''
    text = replace_once(text, old, new, "explicit load more")
    write(path, text)


def main() -> None:
    patch_option_p0()
    patch_option_query()
    patch_frontend()


if __name__ == "__main__":
    main()
