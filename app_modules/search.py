# search.py — PanSou 搜索 + 缓存 + 结果解析
import time
from config import ConfigManager
from utils import log, TTLCache
from resilience import CircuitBreaker, CircuitBreakerOpen

_pansou_cache = TTLCache(ttl=600, max_size=200)
pansou_breaker = CircuitBreaker("pansou", failure_threshold=5, recovery_timeout=60)


def _get_pansou_client():
    cfg = ConfigManager.get_instance()
    from api_client import PanSouClient
    return PanSouClient(cfg.pansou, timeout=20)


def search_pansou(keyword, category="movie"):
    """搜索 PanSou，带缓存和熔断器"""
    cached = _pansou_cache.get("{}:{}".format(category, keyword))
    if cached is not None:
        return cached
    for attempt in range(2):
        try:
            client = _get_pansou_client()
            data = pansou_breaker.call(client.search, keyword)
            results = data.get("data", {}).get("merged_by_type", {}).get("quark", [])
            if not isinstance(results, list):
                results = data.get("results", [])
            formatted_results = []
            for item in results:
                title = item.get("note", item.get("Title", item.get("title", "")))
                url = item.get("url", item.get("URL", ""))
                if isinstance(url, list) and len(url) > 0:
                    url = url[0].get("url", url[0].get("URL", ""))
                elif isinstance(url, dict):
                    url = url.get("url", url.get("URL", ""))
                if title and url:
                    formatted_results.append({
                        "title": title,
                        "url": url,
                        "source": item.get("source", item.get("Source", "夸克网盘"))
                    })
            _pansou_cache.set("{}:{}".format(category, keyword), formatted_results)
            return formatted_results
        except CircuitBreakerOpen as e:
            log("PanSou 熔断: {}".format(e))
            return []
        except Exception as e:
            if attempt == 0:
                log("PanSou 重试: {}".format(e))
                time.sleep(2)
            else:
                log("PanSou 错误: {}".format(e))
                return []
