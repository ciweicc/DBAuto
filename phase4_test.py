import json
from playwright.sync_api import sync_playwright

URL = "http://localhost:8099/index_new.html"

STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/dashboard/all": {
        "stats": {"today_count": 3, "week_ok": 10, "week_fail": 1, "week_total": 11,
                  "daily": [{"date": "0" + str(i), "ok": 1, "fail": 0} for i in range(1, 8)],
                  "last_status": "success", "last_time": "2024-01-01 10:00:00"},
        "schedule_status": {"transfer_next": "2024-01-01 12:00", "expired_check_next": "2024-01-01 03:00",
                            "last_transfer": "2024-01-01 10:00:00", "last_expired_check": "2024-01-01 03:00"},
        "version": "1.1.0"
    },
    "/api/settings/all": {
        "config": {"pansou": "http://x", "tmdb_api_key": "", "qas": "http://y", "qas_token": "",
                   "auth_user": "root", "douban_uid": "u1", "tmdb_base_url": "http://tmdb"},
        "schedule": {
            "_status": {"transfer_next": "12:00", "expired_check_next": "03:00",
                        "last_transfer": "2024-01-01 10:00:00", "last_expired_check": "2024-01-01 03:00"},
            "_next_runs": {"transfer": "01-01 12:00", "expired_check": "01-01 03:00"},
            "savepaths": {"category_base": "/影视", "search": "/批量转存/手动搜索存", "tmdb": "/批量转存/TMDB"},
            "douban_wish": {"enabled": True, "savepath": "/批量转存/想看", "category": ["movie"],
                            "accounts": [{"uid": "u1", "name": "A", "cookie": "***"}]},
            "transfer": {"enabled": True, "limit": 5, "time": "12:00",
                         "tasks": [{"path": "/影视", "type": "movie"}]},
            "expired_check": {"enabled": True, "time": "03:00", "directories": ["/x"]}
        }
    },
    "/api/exec_history": {"items": []},
    "/api/tmdb/options": {"regions": [{"code": "CN", "name": "中国大陆"}, {"code": "US", "name": "美国"}],
                          "movie_list_types": [{"id": "popular", "name": "热门"}, {"id": "top_rated", "name": "高分"}],
                          "tv_list_types": [{"id": "popular", "name": "热门"}, {"id": "top_rated", "name": "高分"}]},
}

api_requests = []


def rh(route):
    u = route.request.url
    if "/api/" in u:
        path = u.split("8099", 1)[-1].split("?")[0]
        if path.rstrip("/").endswith("/api/sse"):
            route.fulfill(status=200, content_type="text/event-stream", body=b"")
            return
        api_requests.append(path)
        body = STUBS.get(path, {})
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body).encode())
        return
    route.fallback()


console_errors, page_errors = [], []
with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script("try{localStorage.setItem('auth_token','test-token')}catch(e){}")
    page.on("console", lambda m: console_errors.append(m.type + ": " + m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: page_errors.append(str(e)))
    page.route("**/*", rh)

    page.goto(URL, wait_until="load", timeout=15000)
    page.wait_for_timeout(800)

    # 仪表盘默认页渲染校验
    dash_today = page.eval_on_selector("#dashToday", "el => el.textContent")
    dash_next = page.eval_on_selector("#dashNext", "el => el.textContent")
    header_ver = page.eval_on_selector("#headerVersion", "el => el.textContent")
    print("dashToday:", dash_today, "| dashNext:", repr(dash_next), "| headerVersion:", header_ver)

    # 切到调度页 -> 状态条
    page.click('.side-nav-item[data-tab="schedule"]')
    page.wait_for_timeout(300)
    sched_t = page.eval_on_selector("#schedLastTransfer", "el => el.textContent")
    sched_e = page.eval_on_selector("#schedLastExpired", "el => el.textContent")
    print("schedLastTransfer:", sched_t, "| schedLastExpired:", sched_e)

    # 切到设置页（应复用缓存的 SETTINGS_ALL，不再发请求）
    before = len(api_requests)
    page.click('.side-nav-item[data-tab="settings"]')
    page.wait_for_timeout(400)
    cfg_pansou = page.eval_on_selector("#cfg_pansou", "el => el.value")
    cfg_catbase = page.eval_on_selector("#cfg_path_category_base", "el => el.value")
    after = len(api_requests)
    print("cfg_pansou:", cfg_pansou, "| cfg_path_category_base:", cfg_catbase,
          "| settings/all requests added by tab switch:", after - before)

    # 请求清单分析
    from collections import Counter
    counts = Counter(api_requests)
    print("API request counts:", dict(counts))

    # 断言：聚合接口被使用，旧接口不再被首屏使用
    assert counts.get("/api/dashboard/all", 0) == 1, "dashboard/all 应恰好请求 1 次"
    assert counts.get("/api/settings/all", 0) >= 1, "settings/all 至少请求 1 次"
    for old in ("/api/dashboard/stats", "/api/version", "/api/config", "/api/schedule"):
        assert counts.get(old, 0) == 0, "首屏不应再请求旧接口: %s (实际 %s)" % (old, counts.get(old, 0))
    assert dash_today == "3", "dashToday 应为 3"
    assert "12:00" in dash_next and "03:00" in dash_next, "dashNext 应显示转存/检测时间"
    assert header_ver == "v1.1.0", "headerVersion 应为 v1.1.0"
    assert sched_t != "-" and sched_e != "-", "调度状态条应有值"
    assert cfg_pansou == "http://x", "配置应读取 config.pansou"
    assert cfg_catbase == "/影视", "savepaths.category_base 应正确读取"

    print("PAGE ERRORS:", json.dumps(page_errors, ensure_ascii=False))
    print("CONSOLE ERR/WARN:", json.dumps(console_errors[:20], ensure_ascii=False))
    assert not page_errors, "页面存在 JS 错误: " + json.dumps(page_errors)
    print("\nPHASE4_OK: 聚合接口接入且首屏 GET 数下降，数据一致")
    browser.close()
