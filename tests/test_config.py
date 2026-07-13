import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from config import ConfigManager, LOCAL_TZ


class TestConfigManager:
    def setup_method(self):
        """每个测试前重置单例"""
        ConfigManager._instance = None

    def test_get_instance_singleton(self):
        a = ConfigManager.get_instance()
        b = ConfigManager.get_instance()
        assert a is b

    def test_default_config(self):
        mgr = ConfigManager.get_instance()
        cfg = mgr.get_config()
        assert "pansou" in cfg
        assert "qas" in cfg
        assert "qas_token" in cfg
        assert "auth_user" in cfg
        assert "auth_pass" in cfg
        assert cfg["auth_user"] == "root"

    def test_set_and_get_config(self):
        mgr = ConfigManager.get_instance()
        cfg = mgr.get_config()
        cfg["pansou"] = "http://test:8080"
        mgr.set_config(cfg)
        # 重新获取，确认持久化
        ConfigManager._instance = None
        mgr2 = ConfigManager.get_instance()
        cfg2 = mgr2.get_config()
        assert cfg2["pansou"] == "http://test:8080"

    def test_settings_default(self):
        mgr = ConfigManager.get_instance()
        settings = mgr.get_settings()
        assert "transfer" in settings
        assert "expired_check" in settings
        assert settings["transfer"]["enabled"] is False
        assert settings["transfer"]["time"] == "02:00"

    def test_set_and_get_settings(self):
        mgr = ConfigManager.get_instance()
        settings = mgr.get_settings()
        settings["transfer"]["enabled"] = True
        settings["transfer"]["limit"] = 10
        mgr.set_settings(settings)
        ConfigManager._instance = None
        mgr2 = ConfigManager.get_instance()
        s2 = mgr2.get_settings()
        assert s2["transfer"]["enabled"] is True
        assert s2["transfer"]["limit"] == 10

    def test_reload(self):
        mgr = ConfigManager.get_instance()
        mgr.get_config()
        mgr.reload()
        assert mgr._config is None
        assert mgr._settings is None

    def test_local_tz(self):
        assert LOCAL_TZ is not None
