from pathlib import Path

path = Path("back/tests/test_option_query_p1.py")
text = path.read_text(encoding="utf-8")
old = '''    assert overrides[("approvers",)] == [
        {"id": 1, "name": "张经理"},
        {"id": 2, "name": "李经理"},
    ]
'''
new = '''    # Existing array binding intentionally emits only target request fields.
    assert overrides[("approvers",)] == [{"id": 1}, {"id": 2}]
'''
if text.count(old) != 1:
    raise SystemExit("expected array assertion not found exactly once")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
