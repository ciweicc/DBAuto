#!/usr/bin/env python3
"""概览页 DOM 度量走查（UI 路线图第十节 / issue #8）。

用途：
  在真实浏览器里加载构建产物，注入确定性的接口桩数据，
  对概览页首屏的**可量化缺陷**做断言 —— 这些指标用静态扫描
  （tests/test_build.py）表达不了，只能在渲染后测量：

  1. 首屏工作区必须整体落在一屏内（全部块底边 ≤ 视口高，且无内部滚动）；
  2. 指标行等高且副信息不被裁切（曾出现 81/93 参差 + 「失败 86」被挤断）；
  3. 最近转存表不得出现横向滚动条；
  4. 面板内不得出现纵向滚动容器（概览整体滚动交给 .content）；
  5. 跳过状态必须与成功状态有区分（不得全部渲染为「已转存」）；
  6. 空日期条目必须排在列表末尾，且日期格式为 MM/DD HH:MM；
  7. 移动端表格卡片化后，分类前缀不得被压成竖排单字；
  8. 横向溢出（文档 + 内容区）必须为 0。

依赖：playwright + chromium（仓库 E2E 测试同依赖），
  pip install playwright && python -m playwright install chromium
无依赖时脚本以退出码 0 跳过（避免影响不装浏览器的 CI 阶段）。

用法：
  python3 scripts/check_overview_dom.py            # 检查（失败非零退出）
  python3 scripts/check_overview_dom.py --json     # 机器可读报告
  python3 scripts/check_overview_dom.py --serve    # 自动起本地静态服务
"""
import argparse
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "static", "index_new.html")
PORT = 8123
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# ---------------------------------------------------------------- 桩数据
# 与真实接口契约保持一致（app_modules/routes_history.py / tmdb.py）：
#   /api/dashboard/all -> {stats, schedule_status, version}
#   /api/history       -> {total, items:{title:{category,date,shareurl,status}}}
#   /api/tmdb/list     -> {items:[{id,title,poster,year,rating,...}]}
_MOVIE = "示例作品标题-{:02d}"


def _history():
    items = {}
    for i in range(12):
        items[_MOVIE.format(i)] = {
            "category": "movie" if i % 3 else "tv",
            "date": "2026-09-{:02d} 10:{:02d}:00".format(15 - i, i),
            # 每 4 条留一条空链接：真实数据里幂等跳过的条目就是没有 shareurl
            "shareurl": "https://pan.quark.cn/s/demo{:02d}".format(i) if i % 4 else "",
            "status": "ok" if i % 4 else "exists",
        }
    # 空日期条目：存储确实可能给出 date:""，它必须排在最后
    items["无日期条目"] = {"category": "movie", "date": "", "shareurl": "", "status": "exists"}
    return {"total": len(items), "items": items}


def _recommendations():
    out = []
    for i in range(12):
        out.append({
            "id": 1000 + i,
            "title": "热门影片名称较长展示用-{:02d}".format(i),
            "rating": round(7.0 + (i % 4) * 0.4, 1),
            "votes": 1200 + i * 37,
            "year": 2020 + (i % 5),
            "overview": "简介",
            "poster": "https://example.invalid/p{}.jpg".format(i) if i % 2 == 0 else "",
        })
    return {"items": out, "total_pages": 3, "total_results": 60, "page": 1}


STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/dashboard/all": {
        "stats": {"today_count": 3, "week_ok": 10, "week_fail": 1, "week_total": 11,
                  "daily": [{"date": "09-0{}".format(i), "ok": 1, "fail": 0} for i in range(1, 8)],
                  "last_status": "success", "last_time": "2026-09-15 10:00:00"},
        "schedule_status": {"transfer_next": "2026-09-15 12:00",
                            "expired_check_next": "2026-09-15 03:00",
                            "last_transfer": "2026-09-15 10:00:00",
                            "last_expired_check": "2026-09-15 03:00"},
        "version": "1.1.0",
    },
    "/api/history": _history(),
    "/api/tmdb/list": _recommendations(),
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/tmdb/options": {"regions": [{"code": "CN", "name": "中国大陆"}],
                          "movie_list_types": [{"id": "popular", "name": "热门"}],
                          "tv_list_types": [{"id": "popular", "name": "热门"}]},
}

MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const content = q('.content');
  const rows = [...document.querySelectorAll('.ov-stat-row')];
  const recRows = [...document.querySelectorAll('#ovRecentBody tr')];
  const scrollers = [...document.querySelectorAll('#pageOverview *')].filter(e =>
    e.scrollHeight > e.clientHeight + 1 && getComputedStyle(e).overflowY !== 'visible'
  ).map(e => e.className || e.tagName);
  const cat = q('#ovRecentBody .ov-table-cat');
  return {
    contentH: Math.round(content.scrollHeight),
    contentClientH: Math.round(content.clientHeight),
    viewportH: window.innerWidth >= 640 ? window.innerHeight : 0,
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    contentOverflowX: content.scrollWidth - content.clientWidth,
    lastBlockBottom: Math.round([...document.querySelectorAll('#pageOverview > *')]
      .map(e => e.getBoundingClientRect().bottom).reduce((a, b) => Math.max(a, b), 0)),
    statRowH: rows.map(r => Math.round(r.getBoundingClientRect().height)),
    statRowClipped: rows.filter(r => r.querySelector('.ov-stat-line') &&
      r.querySelector('.ov-stat-line').scrollWidth > r.querySelector('.ov-stat-line').clientWidth + 1).length,
    tableOverflowX: (() => { const w = q('.ov-table-wrap'); return w ? w.scrollWidth - w.clientWidth : 0; })(),
    scrollers,
    recentRows: recRows.length,
    statuses: [...new Set([...document.querySelectorAll('#ovRecentBody .ov-badge')].map(e => e.textContent))],
    firstDate: (() => { const e = q('#ovRecentBody .ov-table-date'); return e ? e.textContent : null; })(),
    lastDate: (() => { const all = document.querySelectorAll('#ovRecentBody .ov-table-date');
      return all.length ? all[all.length - 1].textContent : null; })(),
    lastDateText: (() => { const all = [...document.querySelectorAll('#ovRecentBody tr')];
      return all.length ? all[all.length - 1].textContent : null; })(),
    catW: cat ? Math.round(cat.getBoundingClientRect().width) : null,
    catH: cat ? Math.round(cat.getBoundingClientRect().height) : null,
  };
}
"""

VIEWPORTS = [
    # (宽度, 高度, 需要「一屏放下」, 标签)
    (1920, 1080, True, "1920x1080 大屏"),
    (1600, 900, True, "1600x900"),
    (1440, 900, True, "1440x900"),
    (1366, 900, True, "1366x900"),
    (1280, 900, True, "1280x900"),
    (1200, 900, True, "1200x900 日志栏转覆盖层"),
    (1024, 900, True, "1024x900 侧栏收起"),
    (760, 900, False, "760x900 首屏两列堆叠"),
    (390, 844, False, "390x844 移动端"),
]


def _start_server():
    import http.server
    import functools

    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        """静态服务：静默访问日志，避免日志混进度量报告。"""

        def log_message(self, *a):
            pass

    handler = functools.partial(_QuietHandler, directory=os.path.join(ROOT, "static"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--serve", action="store_true", help="自动起本地静态服务（否则需外部服务）")
    args = ap.parse_args()

    if not os.path.isfile(DIST):
        print("跳过：未找到构建产物 static/index_new.html")
        return 0
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("跳过：未安装 playwright（pip install playwright && python -m playwright install chromium）")
        return 0

    httpd = _start_server() if args.serve else None
    problems, report = [], []

    def handler(route):
        url = route.request.url
        if "/api/" in url:
            path = url.split(str(PORT), 1)[-1].split("?")[0]
            if path.rstrip("/").endswith("/api/sse"):
                route.fulfill(status=200, content_type="text/event-stream", body=b"")
                return
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(STUBS.get(path, {})).encode())
            return
        route.fallback()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            for width, height, on_screen, label in VIEWPORTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", handler)
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(700)
                m = page.evaluate(MEASURE)
                m["label"] = label
                m["width"] = width
                m["height"] = height
                report.append(m)

                def fail(msg):
                    problems.append("[{}] {}".format(label, msg))

                if m["docOverflowX"] > 0:
                    fail("文档横向溢出 {}px".format(m["docOverflowX"]))
                if m["contentOverflowX"] > 0:
                    fail("内容区横向溢出 {}px".format(m["contentOverflowX"]))
                if m["statRowClipped"]:
                    fail("{} 行指标被裁切（副信息不可见）".format(m["statRowClipped"]))
                if len(set(m["statRowH"])) > 1:
                    fail("指标行高不一致：{}".format(m["statRowH"]))
                if m["tableOverflowX"] > 0:
                    fail("最近转存表出现横向滚动条 {}px".format(m["tableOverflowX"]))
                if on_screen and m["lastBlockBottom"] > height:
                    fail("首屏工作区超出视口：底边 {} > {}".format(m["lastBlockBottom"], height))
                if "ov-table-wrap" in m["scrollers"]:
                    fail("最近转存仍在面板内滚动（高度与左侧概览脱钩）")
                if not {"已转存", "已存在跳过"} <= set(m["statuses"]):
                    fail("跳过与成功未区分：{}".format(m["statuses"]))
                if m["firstDate"] and m["firstDate"].count("/") != 1:
                    fail("日期格式不是 MM/DD HH:MM：{}".format(m["firstDate"]))
                if m["lastDateText"] is not None and "无日期条目" in (m["lastDateText"] or ""):
                    fail("空日期条目未排在列表末尾")
                if width <= 640 and m["catW"] is not None and m["catW"] < 24:
                    fail("移动端分类前缀被压成竖排单字（宽 {}px）".format(m["catW"]))
                page.close()
            browser.close()
    finally:
        if httpd is not None:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report}, ensure_ascii=False, indent=2))
    else:
        for m in report:
            print("{:<26} 指标行高 {:<16} 表格溢出 {:<3} 首屏底边 {:<6} 状态 {}".format(
                m["label"], str(m["statRowH"]), m["tableOverflowX"], m["lastBlockBottom"],
                "/".join(m["statuses"])))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p_ in problems:
                print("  - " + p_)
            return 1
        print("概览页 DOM 度量走查通过（{} 组视口，0 处问题）".format(len(report)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
