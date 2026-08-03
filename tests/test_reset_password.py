"""P1 密码重置测试：从标准输入读取、拒绝命令行参数、空密码校验。"""
import sys
import io

# sys.path 由 tests/conftest.py 统一配置（也可由 pyproject.toml pythonpath 覆盖）

import reset_password


def test_read_password_from_stdin_non_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("  myPass1  \n"))
    assert reset_password._read_password() == "myPass1"


def test_main_rejects_argv(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["reset_password.py", "secret"])
    try:
        reset_password.main()
        raised = False
    except SystemExit as e:
        raised = True
        assert e.code == 1
    assert raised, "命令行传入密码应被拒绝并退出"
    out = capsys.readouterr().out
    assert "命令行参数" in out


def test_main_reads_stdin_and_saves(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["reset_password.py"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("newpass\n"))
    saved = {}
    monkeypatch.setattr(reset_password, "load_config", lambda: {})
    monkeypatch.setattr(reset_password, "save_config", lambda c: saved.update(c))
    monkeypatch.setattr(reset_password, "hash_auth_password", lambda p: "HASHED")
    reset_password.main()
    assert saved.get("auth_pass") == "HASHED"
    out = capsys.readouterr().out
    assert "重置成功" in out


def test_reset_password_rejects_empty(monkeypatch):
    import pytest
    monkeypatch.setattr(reset_password, "load_config", lambda: {})
    monkeypatch.setattr(reset_password, "save_config", lambda c: None)
    with pytest.raises(SystemExit):
        reset_password.reset_password("")
