from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "back/dano/execution/page/option_p0.py"


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    old = '''    raw_url = str(read.get("url") or select.get("source_url") or "")
    parsed = urlparse(raw_url)
    clean_url = parsed._replace(query="", fragment="").geturl()
    select["source_url"] = clean_url or raw_url
    source_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if source_query:
        select["source_query"] = source_query
'''
    new = '''    raw_url = str(read.get("url") or select.get("source_url") or "")
    # Sanitize before splitting query metadata. Otherwise moving query parameters out
    # of source_url could bypass the P0 compile guard and persist a raw token in
    # source_query. Sensitive sources keep the redacted URL marker so runtime blocks
    # replay of an incomplete authentication flow.
    from dano.execution.page.option_p0_compile_guard import _sanitize_source_url

    sanitized_url, url_markers = _sanitize_source_url(raw_url)
    parsed = urlparse(sanitized_url)
    clean_url = parsed._replace(query="", fragment="").geturl()
    source_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if source_query:
        select["source_query"] = source_query
    select.update(url_markers)
    if url_markers.get("source_sensitive_query_keys") or url_markers.get("source_url_had_credentials"):
        select["source_url"] = sanitized_url
    else:
        select["source_url"] = clean_url or sanitized_url
'''
    PATH.write_text(replace_once(text, old, new), encoding="utf-8")


if __name__ == "__main__":
    main()
