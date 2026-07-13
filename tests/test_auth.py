import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from auth import AuthManager


class TestAuthManager:
    def setup_method(self):
        """每个测试前重置单例"""
        AuthManager._instance = None

    def test_hash_and_verify_password(self):
        hashed = AuthManager.hash_password("test123")
        assert hashed.startswith("$pbkdf2$")
        mgr = AuthManager.get_instance()
        assert mgr._verify_pass("test123", hashed) is True
        assert mgr._verify_pass("wrong", hashed) is False

    def test_verify_empty_password(self):
        mgr = AuthManager.get_instance()
        assert mgr._verify_pass("", "") is True
        assert mgr._verify_pass("something", "") is False

    def test_login_rate_limiting(self):
        mgr = AuthManager.get_instance()
        ip = "192.168.1.100"
        # 前 4 次允许
        for i in range(4):
            ok, wait = mgr.check_login_rate(ip)
            assert ok is True
        # 第 5 次锁定
        ok, wait = mgr.check_login_rate(ip)
        assert ok is False
        assert wait > 0

    def test_login_rate_limit_reset_after_window(self):
        mgr = AuthManager.get_instance()
        ip = "10.0.0.50"
        # 模拟窗口过期后重置
        ok, _ = mgr.check_login_rate(ip)
        assert ok is True
        # 手动设置旧时间戳
        with mgr._login_lock:
            mgr._login_attempts[ip]["first"] = 0
        ok, _ = mgr.check_login_rate(ip)
        assert ok is True

    def test_token_generation_and_check(self):
        mgr = AuthManager.get_instance()
        token = mgr._gen_token()
        assert len(token) == 32
        assert token != mgr._gen_token()

    def test_get_client_ip_direct(self):
        """直接连接时返回 client_address"""
        class FakeHandler:
            client_address = ("192.168.1.5", 12345)
            headers = {}
        ip = AuthManager.get_client_ip(FakeHandler)
        assert ip == "192.168.1.5"
