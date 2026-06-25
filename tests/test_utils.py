import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

from utils import TTLCache

class TestTTLCache:
    def test_cache_set_get(self):
        cache = TTLCache(ttl=300)
        cache.set("key", "value")
        assert cache.get("key") == "value"

    def test_cache_ttl(self):
        cache = TTLCache(ttl=0.1)
        cache.set("key", "value")
        assert cache.get("key") == "value"
        import time
        time.sleep(0.2)
        assert cache.get("key") is None

    def test_cache_max_size(self):
        cache = TTLCache(max_size=3)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        assert cache.get("key1") == "value1"
        cache.set("key4", "value4")
        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"

    def test_cache_clear(self):
        cache = TTLCache()
        cache.set("key", "value")
        assert cache.get("key") == "value"
        cache.clear()
        assert cache.get("key") is None

    def test_cache_contains(self):
        cache = TTLCache()
        cache.set("key", "value")
        assert "key" in cache
        assert "nonexistent" not in cache

    def test_cache_len(self):
        cache = TTLCache()
        assert len(cache) == 0
        cache.set("key1", "value1")
        assert len(cache) == 1
        cache.set("key2", "value2")
        assert len(cache) == 2