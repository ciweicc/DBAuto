import json
from playwright.sync_api import sync_playwright

URL = "http://localhost:8099/index_new.html"
SAMPLE_HISTORY = {"items":[
  {"id":3,"type":"transfer","detail":"电影榜单转存","status":"ok","time":"2024-03-02 12:00:00",
   "data":{"ok":5,"failed":0,"skipped":1,"results":[{"title":"电影A","status":"ok","category":"电影"}]}},
  {"id":2,"type":"expired_check","detail":"失效链接检测","status":"partial","time":"2024-03-01 03:00:00",
   "data":{"expired":[{"title":"失效B","path":"/x/B"}]}},
  {"id":1,"type":"config","detail":"配置已更新","status":"ok","time":"2024-02-28 10:00:00","data":{}}
]}
STUBS = {
  "/api/categories": {"movie": [], "tv": [], "variety": []},
  "/api/dashboard/stats": {"today_count": 3, "week_ok": 10, "week_fail": 1, "week_total": 11,
                            "daily": [{"date": "0"+str(i), "ok": 1, "fail": 0} for i in range(1, 8)],
                            "last_status": "ok", "last_time": "2024-01-01 10:00:00"},
  "/api/schedule": {"_status": {"transfer_next": "12:00", "expired_check_next": "03:00",
                                 "last_transfer": "2024-03-02 12:00:00", "last_expired_check": "2024-03-01 03:00:00"},
                      "_next_runs": []},
  "/api/exec_history": SAMPLE_HISTORY,
  "/api/config": {"tmdb_api_key": "", "wish_enabled": False},
  "/api/tmdb/options": {"regions": [{"code": "CN", "name": "中国大陆"}],
                        "movie_list_types": [{"id": "popular", "name": "热门"}],
                        "tv_list_types": [{"id": "popular", "name": "热门"}]},
  "/api/version": "v1.1.0",
}

def rh(route):
    u = route.request.url
    if "/api/" in u:
        path = u.split("8099", 1)[-1].split("?")[0]
        if path.rstrip("/").endswith("/api/sse"):
            route.fulfill(status=200, content_type="text/event-stream", body=b""); return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(STUBS.get(path, {})).encode())
        return
    route.fallback()

errs, perrs = [], []
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
    pg.on("console", lambda m: errs.append(m.type+": "+m.text) if m.type in ("error","warning") else None)
    pg.on("pageerror", lambda e: perrs.append(str(e)))
    pg.route("**/*", rh)
    pg.goto(URL, wait_until="load", timeout=15000)
    pg.wait_for_timeout(800)

    # History table
    pg.click('.side-nav-item[data-tab="history"]')
    pg.wait_for_timeout(400)
    tbl = pg.query_selector("#execHistoryTable")
    print("history table present:", tbl is not None)
    if tbl:
        ths = pg.eval_on_selector_all("#execHistoryTable thead th", "els => els.map(e => ({t:e.textContent, sort:e.getAttribute('data-sort')}))")
        print("thead:", ths)
        rows = pg.eval_on_selector_all("#execHistoryTable tbody tr.hist-row", "els => els.length")
        print("history rows:", rows)
        # status dot classes present
        dots = pg.eval_on_selector_all("#execHistoryTable tbody .status-dot-cell", "els => els.map(e=>e.className)")
        print("status dots:", dots)
        # click first row -> detail expands
        pg.click("#execHistoryTable tbody tr.hist-row")
        pg.wait_for_timeout(200)
        detail_disp = pg.eval_on_selector("#hist_detail_3", "el => getComputedStyle(el).display")
        print("detail row display after click (expect table-row):", detail_disp)
        # sort by time header
        pg.click('#execHistoryTable thead th[data-sort="time"]')
        pg.wait_for_timeout(200)
        ind = pg.eval_on_selector('#execHistoryTable thead th[data-sort="time"] .sort-ind', "el => el.textContent")
        print("time sort indicator after click (expect ▲ for asc):", ind)

    # Schedule status bar
    pg.click('.side-nav-item[data-tab="schedule"]')
    pg.wait_for_timeout(400)
    lt = pg.eval_on_selector("#schedLastTransfer", "el => el.textContent")
    le = pg.eval_on_selector("#schedLastExpired", "el => el.textContent")
    print("schedLastTransfer:", lt, "| schedLastExpired:", le)

    print("PAGE ERRORS:", perrs)
    print("CONSOLE ERR/WARN:", [e for e in errs if e.startswith('error')][:10])
    b.close()
