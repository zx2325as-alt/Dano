from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def update(path: str, transform) -> None:
    file = ROOT / path
    original = file.read_text(encoding="utf-8")
    changed = transform(original)
    if changed == original:
        raise RuntimeError(f"{path}: expected change was not applied")
    file.write_text(changed, encoding="utf-8")


def patch_frontend(text: str) -> str:
    text = text.replace("\x00", "\\u0000")
    old = '    if (!skill || optionLoading[key]) return;\n'
    new = '    if (!skill) return;\n    if (optionLoading[key] && !args.force) return;\n'
    if old not in text:
        raise RuntimeError("InvokeDrawer loading guard not found")
    return text.replace(old, new, 1)


def patch_option_query(text: str) -> str:
    old_sig = '''def _apply_source_bindings(select: dict, *, query: str, context: dict,
                           limit: int, offset: int) -> tuple[dict, list[str], bool]:
'''
    new_sig = '''def _apply_source_bindings(select: dict, *, query: str, context: dict,
                           limit: int, offset: int) -> tuple[dict, list[str], bool, bool]:
'''
    if old_sig not in text:
        raise RuntimeError("option binding signature not found")
    text = text.replace(old_sig, new_sig, 1)
    text = text.replace(
        '    used_query = False\n    body, body_kind = _body_object(bound)\n',
        '    used_query = False\n    used_pagination = False\n    body, body_kind = _body_object(bound)\n',
        1,
    )
    text = text.replace(
        '        if source == "query":\n            used_query = True\n',
        '        if source == "query":\n            used_query = True\n        if source in {"limit", "offset"}:\n            used_pagination = True\n',
        1,
    )
    text = text.replace(
        '    return bound, list(dict.fromkeys(missing)), used_query\n',
        '    return bound, list(dict.fromkeys(missing)), used_query, used_pagination\n',
        1,
    )
    text = text.replace(
        '    bound, missing, used_upstream_query = _apply_source_bindings(\n',
        '    bound, missing, used_upstream_query, used_upstream_pagination = _apply_source_bindings(\n',
        1,
    )
    old_page = '''    total = len(filtered)
    page = filtered[offset:offset + limit]
    next_offset = offset + len(page)
    has_more = next_offset < total
    next_cursor = _encode_cursor(next_offset, fingerprint) if has_more else None
'''
    new_page = '''    if used_upstream_pagination:
        # The target source already consumed offset/limit. Do not apply the same offset
        # twice. A full page means another page may exist; execution still revalidates
        # the final submitted value against the live source.
        page = filtered[:limit]
        next_offset = offset + len(page)
        has_more = len(page) >= limit
        total = next_offset + (1 if has_more else 0)
    else:
        total = len(filtered)
        page = filtered[offset:offset + limit]
        next_offset = offset + len(page)
        has_more = next_offset < total
    next_cursor = _encode_cursor(next_offset, fingerprint) if has_more else None
'''
    if old_page not in text:
        raise RuntimeError("option pagination block not found")
    return text.replace(old_page, new_page, 1)


def main() -> None:
    update("skillfrontend/src/components/InvokeDrawer.tsx", patch_frontend)
    update("back/dano/execution/page/option_query.py", patch_option_query)


if __name__ == "__main__":
    main()
