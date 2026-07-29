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
        "schedule": {"_status": {"transfer_next": "2024-01-01 12:00", "expired_check_next": "2024-01-01 03:00",
                                 "last_transfer": "2024-01-01 10:00:00", "last_expired_check": "2024-01-01 03:00"},
                     "_next_runs": {}, "savepaths": {"category_base": "/影视", "search": "/s", "tmdb": "/t"},
                     "douban_wish": {"enabled": False, "category": ["movie"], "accounts": []},
                     "transfer": {"enabled": True, "limit": 5, "tasks": []}, "expired_check": {"enabled": True, "directories": []}}
    },
    "/api/exec_history": {"items": []},
    "/api/tmdb/options": {"regions": [{"code": "CN", "name": "中国大陆"}],
                          "movie_list_types": [{"id": "popular", "name": "热门"}],
                          "tv_list_types": [{"id": "popular", "name": "热门"}]},
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
    page = browser.new_page(viewport={"width": 375, "height": 720}, is_mobile=True)
    page.add_init_script("try{localStorage.setItem('auth_token','test-token')}catch(e){}")
    page.on("console", lambda m: console_errors.append(m.type + ": " + m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: page_errors.append(str(e)))
    page.route("**/*", rh)
    page.goto(URL, wait_until="load", timeout=15000)
    page.wait_for_timeout(800)

    def disp(sel):
        return page.eval_on_selector(sel, "el => getComputedStyle(el).display")

    def has_open(sel):
        return page.eval_on_selector(sel, "el => el.classList.contains('open')")

    def tf(sel):
        return page.eval_on_selector(sel, "el => getComputedStyle(el).transform")

    # 移动端主导航与切换按钮可见
    print("side-toggle display:", disp(".side-toggle"))
    print("bottom-nav display:", disp(".bottom-nav"))
    print("log-fab display:", disp(".log-fab"))
    print("search-toggle-btn display:", disp(".search-toggle-btn"))

    # 默认：侧边栏抽屉收起（translateX 为负），日志 FAB 存在
    sb_tf = tf(".sidebar")
    print("sidebar transform (default):", sb_tf)
    assert sb_tf.startswith("matrix") and "-220" in sb_tf, "sidebar 默认应收起(translateX 负)"

    # 点击汉堡 → 侧边栏打开 + 遮罩出现
    page.click(".side-toggle")
    page.wait_for_timeout(200)
    print("sidebar open after toggle:", has_open(".sidebar"))
    assert has_open(".sidebar"), "点击侧边栏切换按钮应打开抽屉"
    assert page.eval_on_selector("#overlayScrim", "el => el.classList.contains('show')"), "打开抽屉应显示遮罩"

    # 点击遮罩右侧区域（避开侧边栏 0-220px）→ 关闭抽屉
    page.mouse.click(330, 400)
    page.wait_for_timeout(200)
    assert not has_open(".sidebar"), "点击遮罩应关闭抽屉"
    assert not page.eval_on_selector("#overlayScrim", "el => el.classList.contains('show')"), "关闭后遮罩应消失"

    # 日志 FAB → 日志面板打开（bottom-sheet）
    page.click("#logFab")
    page.wait_for_timeout(200)
    print("log-panel open after FAB:", has_open(".log-panel"))
    assert has_open(".log-panel"), "点击日志 FAB 应打开日志面板"
    # 关闭日志面板（遮罩消失后 FAB 被遮罩覆盖，用 closeDrawers 关闭）
    page.evaluate("closeDrawers()")
    page.wait_for_timeout(200)
    assert not has_open(".log-panel"), "closeDrawers 应关闭日志面板"

    # 底部导航切换（移动端主导航）
    page.click('.bottom-nav .nav-item:nth-child(2)')  # 定时任务
    page.wait_for_timeout(200)
    sched_visible = page.eval_on_selector("#tabSchedule", "el => getComputedStyle(el).display !== 'none'")
    print("schedule visible after bottom-nav click:", sched_visible)
    assert sched_visible, "底部导航应切换到定时任务页"

    # ≤480px 搜索全屏浮层
    page.set_viewport_size({"width": 420, "height": 720})
    page.wait_for_timeout(200)
    page.click(".search-toggle-btn")
    page.wait_for_timeout(200)
    print("search-wrapper mobile-show:", page.eval_on_selector(".search-wrapper", "el => el.classList.contains('mobile-show')"))
    assert page.eval_on_selector(".search-wrapper", "el => el.classList.contains('mobile-show')"), "≤480px 搜索应弹全屏浮层"

    print("PAGE ERRORS:", json.dumps(page_errors, ensure_ascii=False))
    print("CONSOLE ERR/WARN:", json.dumps(console_errors[:20], ensure_ascii=False))
    assert not page_errors, "页面存在 JS 错误: " + json.dumps(page_errors)
    print("\nPHASE5_OK: 移动端抽屉/底部导航/日志面板/搜索浮层均正常")
    browser.close()
