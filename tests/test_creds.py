import json, creds

def test_zyl_auth_failure():
    assert creds.is_auth_failure("zhongyulian", json.dumps({"ok": -2, "error": "x"}), 200)

def test_zyl_ok_not_failure():
    assert not creds.is_auth_failure("zhongyulian", json.dumps({"ok": 1}), 200)

# Review Focus: 城市为空的非 JSON Fatal error 不是鉴权失败
def test_zyl_fatal_html_not_auth():
    assert not creds.is_auth_failure("zhongyulian", "<b>Fatal error</b>", 200)

def test_flashx_401():
    assert creds.is_auth_failure("flashx", "", 401)
    assert creds.is_auth_failure("flashx", json.dumps({"code": 403}), 200)

def test_flashx_ok():
    assert not creds.is_auth_failure("flashx", json.dumps({"success": True, "code": 200}), 200)

def test_save_sets_600(tmp_path, monkeypatch):
    p = tmp_path / "creds.json"; monkeypatch.setattr(creds, "PATH", str(p))
    creds.save({"a": 1})
    assert oct(p.stat().st_mode)[-3:] == "600"
    assert creds.load() == {"a": 1}
