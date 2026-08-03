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
        "config": {"pansou": "http://x", "tmdb_api_key": "***", "qas": "http://y", "qas_token": "",
                   "auth_user": "root", "douban_uid": "u1", "tmdb_base_url": "http://tmdb"},
        "schedule": {"_status": {"transfer_next": "2024-01-01 12:00", "expired_check_next": "2024-01-01 03:00",
                                 "last_transfer": "2024-01-01 10:00:00", "last_expired_check": "2024-01-01 03:00"},
                     "_next_runs": {}, "savepaths": {"category_base": "/影视", "search": "/s", "tmdb": "/t"},
                     "douban_wish": {"enabled": False, "category": ["movie"], "accounts": []},
                     "transfer": {"enabled": True, "limit": 5, "tasks": []}, "expired_check": {"enabled": True, "directories": []}}
    },
    "/api/exec_history": {"items": []},
    "/api/config": {"tmdb_api_key": "***", "tmdb_base_url": "http://tmdb"},
    "/api/transfer/status": {"running": False, "stats": {}},
    "/api/tmdb/options": {"regions": [{"code": "CN", "name": "中国大陆"}, {"code": "US", "name": "美国"}],
                          "movie_list_types": [{"id": "popular", "name": "热门"}, {"id": "discover", "name": "发现"}],
                          "tv_list_types": [{"id": "popular", "name": "热门"}, {"id": "discover", "name": "发现"}]},
    "/api/tmdb/genres": {"genres": [{"id": 28, "name": "动作"}, {"id": 35, "name": "喜剧"}]},
    "/api/tmdb/list": {"items": [
        {"id": 1, "title": "测试电影A", "poster": "", "rating": 8.1, "year": 2023, "votes": 1200, "overview": "简介A"},
        {"id": 2, "title": "测试电影B", "poster": "", "rating": 7.5, "year": 2022, "votes": 900, "overview": "简介B"}],
    "total_pages": 1},
    "/api/tmdb/refresh": {"message": "ok"},
    "/api/transfer": {"success": True},
}


def rh(route):
    u = route.request.url
    if "/api/" in u:
        path = u.split("8099", 1)[-1].split("?")[0]
        if path.rstrip("/").endswith("/api/sse"):
            route.fulfill(status=200, content_type="text/event-stream", body=b"")
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(STUBS.get(path, {})).encode())
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
    page.wait_for_timeout(1200)

    # 1) 导航：侧边栏 5 项，无 TMDB；默认「概览」active
    nav_count = page.eval_on_selector_all(".side-nav .side-nav-item", "els => els.length")
    nav_labels = page.eval_on_selector_all(".side-nav .side-nav-item", "els => els.map(e => e.textContent.trim())")
    tmdb_nav = page.query_selector('.side-nav-item[data-tab="tmdb"]')
    overview_active = page.eval_on_selector('.side-nav-item[data-tab="dashboard"]', "el => el.classList.contains('active')")
    print("side-nav count:", nav_count, "| labels:", nav_labels)
    print("tmdb side-nav exists:", tmdb_nav is not None, "| overview active:", overview_active)
    assert nav_count == 5, "侧边栏应仅 5 项"
    assert tmdb_nav is None, "不应再有 TMDB 侧边栏项"
    assert overview_active, "概览应为默认 active"
    assert "概览" in nav_labels and "TMDB" not in nav_labels, "标签应为 概览 且无 TMDB"

    # 2) 默认页同时含指标卡 与 TMDB 网格
    has_dash = page.query_selector("#dashToday") is not None
    has_grid = page.query_selector("#tmdbGrid") is not None
    print("overview has #dashToday:", has_dash, "| has #tmdbGrid:", has_grid)
    assert has_dash and has_grid, "概览页应同时含指标卡与 TMDB 网格"

    # 3) TMDB 网格渲染出卡片（initTmdbPage 已默认触发）
    cards = page.eval_on_selector_all("#tmdbGrid .tmdb-card", "els => els.length")
    dash_today = page.eval_on_selector("#dashToday", "el => el.textContent")
    print("tmdb cards rendered:", cards, "| dashToday:", dash_today)
    assert cards == 2, "TMDB 网格应渲染 2 张卡片"
    assert dash_today == "3", "指标卡 today_count 应为 3"

    # 4) 选片 -> 选择条 active
    page.click("#tmdbGrid .tmdb-card")
    page.wait_for_timeout(200)
    sel_active = page.eval_on_selector("#tmdbSelBar", "el => el.classList.contains('active')")
    sel_count = page.eval_on_selector("#tmdbSelCount", "el => el.textContent")
    print("sel bar active:", sel_active, "| count:", sel_count)
    assert sel_active, "选片后选择条应 active"
    assert "1" in sel_count, "应选择 1 部"

    # 5) 开始转存 -> 切到手动页（日志面板常驻）
    page.click("#tmdbSelBar .btn-primary")
    page.wait_for_timeout(400)
    manual_active = page.eval_on_selector("#tabManual", "el => getComputedStyle(el).display !== 'none'")
    print("after transfer, manual tab visible:", manual_active)
    assert manual_active, "转存应切换到手动页"

    # 6) 切回概览，TMDB 仍在
    page.click('.side-nav-item[data-tab="dashboard"]')
    page.wait_for_timeout(400)
    grid_still = page.eval_on_selector_all("#tmdbGrid .tmdb-card", "els => els.length")
    print("back to overview, tmdb cards:", grid_still)
    assert grid_still == 2, "切回概览后 TMDB 网格应仍在"

    # 7) 其它页可正常切换
    for t in ("manual", "schedule", "history", "settings"):
        page.click('.side-nav-item[data-tab="%s"]' % t)
        page.wait_for_timeout(150)
        vis = page.eval_on_selector("#tab%s" % t.capitalize() if t != "manual" else "#tabManual",
                                    "el => getComputedStyle(el).display !== 'none'")
        assert vis, "%s 页应可见" % t

    print("PAGE ERRORS:", json.dumps(page_errors, ensure_ascii=False))
    print("CONSOLE ERR/WARN:", json.dumps(console_errors[:20], ensure_ascii=False))
    assert not page_errors, "页面存在 JS 错误: " + json.dumps(page_errors)
    print("\nMERGE_OK: 仪表盘与 TMDB 已合并为「概览」，导航 5 项，TMDB 区块正常")
    browser.close()
