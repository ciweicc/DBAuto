# dedup.py — 去重策略（精确匹配 / 清洗匹配 / 子串匹配）+ QAS 缓存
import re
import time
from threading import Lock, local
from config import ConfigManager
from utils import log
from resilience import CircuitBreaker, CircuitBreakerOpen

_qas_cache = set()
_qas_cache_lock = Lock()
qas_breaker = CircuitBreaker("qas", failure_threshold=3, recovery_timeout=60)

_qas_thread_local = local()
_qas_client_lock = Lock()
_qas_client_version = 0


def _get_qas_client():
    cfg = ConfigManager.get_instance()
    cached_version = getattr(_qas_thread_local, "version", -1)
    if cached_version != _qas_client_version or not hasattr(_qas_thread_local, "client"):
        from api_client import QASClient
        _qas_thread_local.client = QASClient(cfg.qas, cfg.qas_token, timeout=20)
        _qas_thread_local.version = _qas_client_version
        with _qas_client_lock:
            log("QAS Client 创建，token 长度: {}".format(len(cfg.qas_token or "")))
    return _qas_thread_local.client


def reset_qas_client():
    global _qas_client_version
    with _qas_client_lock:
        _qas_client_version += 1
    init_qas_cache()


def init_qas_cache():
    for attempt in range(3):
        try:
            client = _get_qas_client()
            data = qas_breaker.call(client.get_data)
            tasks = data.get("data", {}).get("tasklist", [])
            with _qas_cache_lock:
                _qas_cache.clear()
                for t in tasks:
                    _qas_cache.add(t.get("taskname", ""))
            log("QAS: {} 个任务已缓存".format(len(_qas_cache)))
            return
        except CircuitBreakerOpen as e:
            log("QAS 熔断: {}".format(e))
            return
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                log("QAS 初始化错误: {}".format(e))


def is_in_qas(name):
    with _qas_cache_lock:
        return name in _qas_cache


def add_to_qas(name):
    with _qas_cache_lock:
        _qas_cache.add(name)


def get_qas_cache_snapshot():
    """返回 QAS 缓存的快照列表"""
    with _qas_cache_lock:
        return list(_qas_cache) if _qas_cache else []


def _clean_title(title):
    """清洗标题：移除标点和特殊字符，转小写"""
    return re.sub(r'[^\u4e00-\u9fff0-9a-zA-Z]', '', title).lower()


def build_history_index(history, qas_cache=None):
    """构建历史索引，用于快速去重判断"""
    index = {
        "exact": set(history.keys()),
        "clean": set()
    }
    for k in history:
        index["clean"].add(_clean_title(k))
    index["items"] = [(k, _clean_title(k)) for k in history]
    if qas_cache:
        index["qas_clean"] = set()
        for name in qas_cache:
            index["qas_clean"].add(_clean_title(name))
        index["qas_items"] = [(name, _clean_title(name)) for name in qas_cache]
    else:
        index["qas_clean"] = set()
        index["qas_items"] = []
    return index


def find_in_history(title, history, index=None):
    """检查标题是否已存在于历史或 QAS 缓存中

    匹配策略：精确 → 清洗后精确 → 子串匹配（≥3字符）
    """
    if title in history:
        return True
    if index:
        if title in index["exact"]:
            return True
        title_clean = _clean_title(title)
        if title_clean in index["clean"]:
            return True
        for k, k_clean in index["items"]:
            if title_clean == k_clean or (len(title_clean) >= 3 and title_clean in k_clean) or (len(k_clean) >= 3 and k_clean in title_clean):
                return True
        for name, name_clean in index["qas_items"]:
            if title_clean == name_clean or (len(title_clean) >= 3 and title_clean in name_clean) or (len(name_clean) >= 3 and name_clean in title_clean):
                return True
        return False
    title_clean = _clean_title(title)
    for k in history:
        k_clean = _clean_title(k)
        if title_clean == k_clean or (len(title_clean) >= 3 and title_clean in k_clean) or (len(k_clean) >= 3 and k_clean in title_clean):
            return True
    return False
