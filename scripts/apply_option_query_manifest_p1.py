from pathlib import Path

path = Path("back/dano/catalog/manifest.py")
text = path.read_text(encoding="utf-8")
anchor = '''    return opts


def _schema_prop(skill: SkillSpec, field: str, desc: str, sel: dict | None = None) -> dict:
'''
helper = '''    return opts


def _option_query_schema(sel: dict | None) -> dict:
    """Project safe query capabilities without exposing source URL/body details."""
    protocol = (sel or {}).get("option_query") or {}
    if not isinstance(protocol, dict):
        return {}
    search = protocol.get("search") if isinstance(protocol.get("search"), dict) else None
    pagination = protocol.get("pagination") if isinstance(protocol.get("pagination"), dict) else None
    dependencies = protocol.get("dependencies") if isinstance(protocol.get("dependencies"), list) else []
    out = {
        "x-options-search": bool(search),
        "x-options-depends-on": list(dict.fromkeys(
            str(item.get("field"))
            for item in dependencies
            if isinstance(item, dict) and item.get("field")
        )),
        "x-options-validation": isinstance(protocol.get("validation"), dict),
    }
    if search:
        out["x-options-min-query-length"] = max(0, int(search.get("min_length") or 0))
    if pagination:
        mode = str(pagination.get("mode") or "page").lower()
        if mode in {"page", "offset", "cursor"}:
            out["x-options-pagination"] = mode
    return out


def _schema_prop(skill: SkillSpec, field: str, desc: str, sel: dict | None = None) -> dict:
'''
if text.count(anchor) != 1:
    raise SystemExit("manifest helper anchor not found exactly once")
text = text.replace(anchor, helper, 1)
old = '''        if (sel or {}).get("source_url"):
            prop["x-options-source"] = True
        if opts:
'''
new = '''        if (sel or {}).get("source_url"):
            prop["x-options-source"] = True
        prop.update(_option_query_schema(sel))
        if opts:
'''
if text.count(old) != 2:
    raise SystemExit(f"expected two option schema branches, found {text.count(old)}")
path.write_text(text.replace(old, new), encoding="utf-8")
