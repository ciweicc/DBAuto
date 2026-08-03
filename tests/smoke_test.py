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
            "_status": {"transfer_next": "2024-01-01 12:00", "expired_check_next": "2024-01-01 03:00",
                        "last_transfer": "2024-01-01 10:00:00", "last_expired_check": "2024-01-01 03:00"},
            "_next_runs": {"transfer": "01-01 12:00", "expired_check": "01-01 03:00"},
            "savepaths": {"category_base": "/影视", "search": "/批量转存/手动搜索存", "tmdb": "/批量转存/TMDB"},
            "douban_wish": {"enabled": True, "savepath": "/批量转存/想看", "category": ["movie"],
                            "accounts": [{"uid": "u1", "name": "A", "cookie": "***"}]},
            "transfer": {"enabled": True, "limit": 5, "time": "12:00", "tasks": []},
            "expired_check": {"enabled": True, "time": "03:00", "directories": ["/x"]}
        }
    },
    "/api/exec_history": {"items": []},
    "/api/tmdb/options": {"regions": [{"code": "CN", "name": "中国大陆"}, {"code": "US", "name": "美国"}],
                          "movie_list_types": [{"id": "popular", "name": "热门"}, {"id": "top_rated", "name": "高分"}],
                          "tv_list_types": [{"id": "popular", "name": "热门"}, {"id": "top_rated", "name": "高分"}]},
}

def rh(route):
    u = route.request.url
    if "/api/" in u:
        path = u.split("8099", 1)[-1].split("?")[0]
        if path.rstrip("/").endswith("/api/sse"):
            route.fulfill(status=200, content_type="text/event-stream", body=b"")
            return
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
    page.wait_for_timeout(1000)

    print("URL after load:", page.url)
    print("has .app:", page.query_selector(".app") is not None)

    def box(sel):
        el = page.query_selector(sel)
        return el.bounding_box() if el else None

    print("sidebar box:", box(".sidebar"))
    print("content box:", box(".content"))
    print("log-panel box:", box(".log-panel"))
    print("app grid columns:", page.eval_on_selector(".app", "el => getComputedStyle(el).gridTemplateColumns"))

    tabs = ["dashboard", "manual", "schedule", "history", "settings"]
    results = {}
    for t in tabs:
        page.click(f'.side-nav-item[data-tab="{t}"]')
        page.wait_for_timeout(300)
        pid = "pageDashboard" if t == "dashboard" else "tab" + t.capitalize()
        visible = page.eval_on_selector("#" + pid, "el => getComputedStyle(el).display !== 'none'")
        nav_active = page.eval_on_selector(f'.side-nav-item[data-tab="{t}"]', "el => el.classList.contains('active')")
        results[t] = {"page_visible": visible, "nav_active": nav_active}
    print("tab switch results:", json.dumps(results, ensure_ascii=False))
    print("log-panel visible on settings page:", page.eval_on_selector(".log-panel", "el => getComputedStyle(el).display !== 'none'"))

    # density spot checks
    dash_cards = page.eval_on_selector_all(".dash .dcard", "els => els.length")
    print("dashboard .dcard count:", dash_cards)
    sched_status = page.eval_on_selector("#schedLastTransfer", "el => el.textContent")
    print("schedLastTransfer text:", sched_status)

    print("PAGE ERRORS:", json.dumps(page_errors, ensure_ascii=False))
    print("CONSOLE ERR/WARN:", json.dumps(console_errors[:20], ensure_ascii=False))
    browser.close()
