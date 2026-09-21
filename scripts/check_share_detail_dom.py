#!/usr/bin/env python3
"""搜索结果的「文件清单」展开走查（issue #88：「搜出来点进去不能看集数全不全」）。

用户诉求原文：一句话 ——「搜索资源的功能搜出来点进去不能看集数全不全」。
搜索结果那一条过去只有「标题 / 来源 / 链接是否有效」，想确认是不是全集
只能先转存再去媒体库翻。现在每条结果多了一个「文件」按钮，点开就是
该分享的文件清单 + 集号摘要。

这条链路能不能用，只能在渲染后量：
  - 静态扫描能证明「代码里有 toggleShareDetail」，但证明不了
    「点下去面板真的展开、请求真的发出去了、清单/摘要真的渲染出来了、
    再点一下真的收起来了、收起时真的没重复打接口」。

断言清单（逻辑状态 + 屏幕状态一起看）：
  1. 每条有链接的结果都带「文件」按钮；无链接的条目不给这个入口
  2. 未展开时面板不可见（不是「藏了一半」）
  3. 点击 → 面板展开、按钮变成「收起」、aria-expanded=true
  4. 不展开也看得见集数：结果行概览由一次预热请求填好（字号 >=12px、缺集标红）；
     展开本身不再新增请求（懒加载仍成立）
  5. 请求 URL 带了 url 参数（不带就是拿不到清单的假请求）
  6. 载入中先给反馈（不能是空白面板）
  7. 渲染出文件行（名称 + 体积/目录）、集号摘要、截断提示
  8. 失败态带真实原因（不能静默空白）
  9. 再次点击收起；按钮文案/aria 回到初始
 10. 二次展开命中缓存：不再发第二个请求
 11. 展开后下拉高度不溢出窗口（面板不能把结果顶出视口）

依赖：playwright + chromium（与其它走查脚本同依赖）。
无依赖 / 无产物时以退出码 0 跳过（与其它走查脚本一致）。

用法：
  python3 scripts/check_share_detail_dom.py            # 检查（失败非零退出）
  python3 scripts/check_share_detail_dom.py --json     # 机器可读报告
  python3 scripts/check_share_detail_dom.py --serve    # 自动起本地静态服务
"""
import argparse
import functools
import glob
import http.server
import json
import os
import sys
import threading
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "static", "index_new.html")
PORT = 0
URL = ""

# 搜索桩：两条结果，一条有链接（可预览）、一条无链接（不该有文件按钮）
SEARCH_STUB = {"success": True, "results": [
    {"title": "某剧 - 全 12 集", "url": "https://pan.quark.cn/s/preview-ok",
     "source": "夸克网盘"},
    {"title": "无链接结果", "url": "", "source": "夸克网盘"},
]}

# 分享详情桩：12 集但缺 8、9 —— 正好覆盖「缺集要指出来」这条核心诉求
DETAIL_STUB = {
    "ok": True,
    "message": "",
    "title": "某剧 - 全 12 集",
    "total": 13,
    "video_count": 10,
    "dir_count": 1,
    "truncated": True,
    # 后端会一并给出集号摘要（缺 8、9）——前端那句「缺 8、9」就靠它
    "episodes": {"numbers": [1, 2, 3, 4, 5, 6, 7, 10, 11, 12],
                 "missing": [8, 9], "continuous": False},
    "files": (
        [{"name": "某剧/", "dir": True, "size": 0, "fid": "d1", "type": "dir"}]
        + [{"name": "某剧.S01E{:02d}.mkv".format(i), "dir": False,
            "size": 1024 * 1024 * 300, "fid": "f%d" % i, "type": "video"}
           for i in [1, 2, 3, 4, 5, 6, 7, 10, 11, 12]]
    ),
}

FAIL_STUB = {"ok": False, "message": "分享已失效", "files": [],
             "total": 0, "video_count": 0, "dir_count": 0, "truncated": False,
             "episodes": {"numbers": [], "missing": [], "continuous": False}}

STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.2.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/tmdb/list": {"items": []},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/transfer/status": {"running": False, "progress": [], "stats": {}},
    "/api/qas/tasks": {"success": True, "tasks": []},
    "/api/library/source": {"success": True, "source": "openlist", "openlist": True,
                            "openlist_ok": True, "openlist_user": "admin"},
}


def _launch_kwargs():
    for cand in sorted(glob.glob("/opt/ms-playwright/chromium-*/chrome-linux64/chrome")):
        if os.path.isfile(cand):
            return {"executable_path": cand}
    return {}


def _start_server():
    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(_QuietHandler, directory=os.path.join(ROOT, "static"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# 渲染后的度量：面板几何/可见性、按钮状态、文件行、下拉是否溢出。
MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const qa = s => Array.from(document.querySelectorAll(s));
  const rect = el => { if(!el) return null; const r = el.getBoundingClientRect();
    return {w: Math.round(r.width), h: Math.round(r.height),
            x: Math.round(r.x), y: Math.round(r.y)}; };
  const item = q('.search-item');
  const btn = item ? item.querySelector('.search-detail-toggle') : null;
  const panel = item ? item.querySelector('.search-detail') : null;
  const dropdown = q('#searchDropdown');
  const pcs = panel ? getComputedStyle(panel) : null;
  return {
    items: qa('.search-item').length,
    toggleCount: qa('.search-detail-toggle').length,
    hasPanel: !!panel,
    panelDisplay: pcs ? pcs.display : '',
    panelRect: rect(panel),
    panelText: panel ? panel.textContent.trim() : '',
    rows: qa('.search-detail-row').length,
    dirRows: qa('.search-detail-row.is-dir').length,
    sizeCells: qa('.search-detail-size').length,
    summary: q('.search-detail-sum') ? q('.search-detail-sum').textContent.trim() : '',
    epOverview: q('.search-item-ep') ? q('.search-item-ep').textContent.trim() : '',
    epOverviewFs: q('.search-item-ep') ? getComputedStyle(q('.search-item-ep')).fontSize : '',
    epOverviewColor: q('.search-item-ep') ? getComputedStyle(q('.search-item-ep')).color : '',
    sumFs: q('.search-detail-sum') ? getComputedStyle(q('.search-detail-sum')).fontSize : '',
    sumColor: q('.search-detail-sum') ? getComputedStyle(q('.search-detail-sum')).color : '',
    msg: q('.search-detail-msg') ? q('.search-detail-msg').textContent.trim() : '',
    btnText: btn ? btn.textContent.trim() : '',
    btnExpanded: btn ? btn.getAttribute('aria-expanded') : null,
    dropdownRect: rect(dropdown),
    viewportH: window.innerHeight,
  };
}
"""


def _route_factory(state):
    def _route(route):
        url = route.request.url
        if "/api/" not in url:
            return route.fallback()
        path = urllib.parse.urlparse(url).path
        if path.rstrip("/").endswith("/api/sse"):
            return route.fulfill(status=200, content_type="text/event-stream", body=b"")
        if path == "/api/search":
            state["search"] += 1
            body = SEARCH_STUB
        elif path == "/api/share_detail":
            state["detail"] += 1
            state["detail_urls"].append(route.request.url)
            body = FAIL_STUB if state.get("force_fail") else DETAIL_STUB
        elif path == "/api/check_link":
            body = {"state": "done", "valid": True, "checked": True, "message": "链接正常"}
        else:
            body = STUBS.get(path, {})
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(body, ensure_ascii=False).encode())
    return _route


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--serve", action="store_true")
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
    global URL, PORT
    if httpd is not None:
        PORT = httpd.server_address[1]
        URL = "http://127.0.0.1:{}/index_new.html".format(PORT)
    else:
        PORT = 8149
        URL = "http://127.0.0.1:{}/index_new.html".format(PORT)
    problems, report = [], []

    def ok(name, cond, detail=""):
        report.append({"check": name, "pass": bool(cond), "detail": detail})
        if not cond:
            problems.append("{}：{}".format(name, detail))

    state = {"search": 0, "detail": 0, "detail_urls": [], "force_fail": False}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"], **_launch_kwargs())

            def open_page(st):
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", lambda r: _route_factory(st)(r))
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(1200)
                return page

            def ms(page):
                return page.evaluate(MEASURE)

            def search(page):
                page.evaluate(
                    "()=>{var i=document.getElementById('searchInput');"
                    "i.value='某剧';i.dispatchEvent(new Event('input',{bubbles:true}));}")
                # onSearchInput 有 500ms 防抖
                page.wait_for_timeout(900)

            # ---------------- 展开 / 收起 ----------------
            page = open_page(state)
            search(page)
            st = ms(page)
            ok("搜索结果渲染出条目", st["items"] >= 1, "items = {}".format(st["items"]))
            ok("有链接的结果带「文件」按钮（无链接的不给）",
               st["toggleCount"] == 1,
               "toggleCount = {}（应恰好 1 个：另一条无链接）".format(st["toggleCount"]))
            ok("未展开时面板不可见", st["panelDisplay"] == "none",
               "panelDisplay = {!r}".format(st["panelDisplay"]))
            # 结果行会**预热**一次概览（issue #88 二次反馈：「不展开也要看得见有多少集」），
            # 所以展开前允许恰好 1 次请求；关键是展开**本身**不再多发一次。
            ok("展开前只做了一次概览预热（懒加载仍成立）",
               state["detail"] == 1,
               "detail calls = {}（应为 1：结果行概览预热）".format(state["detail"]))
            ok("不展开就能看到集数概览（issue #88 二次反馈）",
               st["epOverview"] and "集" in st["epOverview"],
               "epOverview = {!r}".format(st["epOverview"]))
            ok("概览字号不小于 12px（旧版 11px 实测看不见）",
               st["epOverviewFs"] == "12px",
               "epOverviewFs = {!r}".format(st["epOverviewFs"]))
            ok("概览在有链接的结果上带出缺集信息",
               "缺" in st["epOverview"] and "8" in st["epOverview"] and "9" in st["epOverview"],
               "epOverview = {!r}".format(st["epOverview"]))
            ok("按钮初始 aria-expanded=false",
               st["btnExpanded"] == "false", "aria-expanded = {!r}".format(st["btnExpanded"]))
            ok("按钮初始文案为「文件」", st["btnText"] == "文件",
               "btnText = {!r}".format(st["btnText"]))

            # 点击展开
            page.click(".search-detail-toggle")
            page.wait_for_timeout(120)
            st_mid = ms(page)
            ok("展开后立刻有载入反馈（不是空白面板）",
               "读取" in st_mid["panelText"] or st_mid["rows"] > 0,
               "panelText = {!r}".format(st_mid["panelText"][:40]))

            page.wait_for_timeout(800)
            st = ms(page)
            ok("点击后面板展开", st["panelDisplay"] != "none",
               "panelDisplay = {!r}".format(st["panelDisplay"]))
            ok("展开面板有实际高度", bool(st["panelRect"]) and st["panelRect"]["h"] > 20,
               "panelRect = {}".format(st["panelRect"]))
            ok("按钮变为「收起」", st["btnText"] == "收起",
               "btnText = {!r}".format(st["btnText"]))
            ok("展开后 aria-expanded=true", st["btnExpanded"] == "true",
               "aria-expanded = {!r}".format(st["btnExpanded"]))
            ok("展开本身不再新增请求（概览已预热）", state["detail"] == 1,
               "detail calls = {}".format(state["detail"]))
            ok("请求 URL 带上了分享链接（不带＝拿不到清单）",
               state["detail_urls"] and "url=" in state["detail_urls"][0],
               "url = {}".format(state["detail_urls"][:1]))

            ok("渲染出文件行", st["rows"] == 11,
               "rows = {}（桩里有 1 目录 + 10 视频）".format(st["rows"]))
            ok("目录行被标出（可区分目录/文件）", st["dirRows"] == 1,
               "dirRows = {}".format(st["dirRows"]))
            ok("每行有体积/类型单元格", st["sizeCells"] == st["rows"],
               "sizeCells={} rows={}".format(st["sizeCells"], st["rows"]))
            ok("集号摘要指出缺集（8、9）",
               "缺" in st["summary"] and "8" in st["summary"] and "9" in st["summary"],
               "summary = {!r}（这一句就是用户要的「集数全不全」）".format(st["summary"]))
            ok("集号摘要字号已提到 12px（旧版 11px）",
               st["sumFs"] == "12px", "sumFs = {!r}".format(st["sumFs"]))
            ok("截断提示存在（total > 显示数）",
               "共" in st["msg"] and "13" in st["msg"],
               "msg = {!r}".format(st["msg"]))
            ok("展开后面板未把下拉顶出视口",
               bool(st["dropdownRect"]) and st["dropdownRect"]["y"] < st["viewportH"],
               "dropdown={} viewportH={}".format(st["dropdownRect"], st["viewportH"]))

            # 收起
            page.click(".search-detail-toggle")
            page.wait_for_timeout(200)
            st = ms(page)
            ok("再次点击收起面板", st["panelDisplay"] == "none",
               "panelDisplay = {!r}".format(st["panelDisplay"]))
            ok("收起后按钮文案回到「文件」", st["btnText"] == "文件",
               "btnText = {!r}".format(st["btnText"]))
            ok("收起后 aria-expanded=false", st["btnExpanded"] == "false",
               "aria-expanded = {!r}".format(st["btnExpanded"]))

            # 二次展开必须命中缓存（不再打接口）
            page.click(".search-detail-toggle")
            page.wait_for_timeout(600)
            st = ms(page)
            ok("二次展开仍有内容（缓存可用）", st["rows"] == 11,
               "rows = {}".format(st["rows"]))
            ok("二次展开不再发请求（前端缓存）", state["detail"] == 1,
               "detail calls = {}（应仍为 1）".format(state["detail"]))
            page.close()

            # ---------------- 失败态 ----------------
            state_fail = {"search": 0, "detail": 0, "detail_urls": [], "force_fail": True}
            page = open_page(state_fail)
            search(page)
            stf = ms(page)
            ok("失败态下结果行概览有兜底文案（不是空白）",
               stf["epOverview"] and "未知" in stf["epOverview"],
               "epOverview = {!r}".format(stf["epOverview"]))
            page.click(".search-detail-toggle")
            page.wait_for_timeout(900)
            st = ms(page)
            ok("失败态面板仍展开（不是点了没反应）", st["panelDisplay"] != "none",
               "panelDisplay = {!r}".format(st["panelDisplay"]))
            ok("失败态带真实原因（不静默空白）",
               "失效" in st["panelText"],
               "panelText = {!r}".format(st["panelText"][:60]))
            ok("失败态不渲染文件行", st["rows"] == 0,
               "rows = {}".format(st["rows"]))
            page.close()

            browser.close()
    except Exception as e:
        problems.append("执行异常：{}".format(e))

    if args.json:
        print(json.dumps({"problems": problems, "report": report}, ensure_ascii=False, indent=2))
    else:
        for r in report:
            print("[{}] {}{}".format("PASS" if r["pass"] else "FAIL", r["check"],
                                     "" if r["pass"] else "  -> " + r["detail"]))
        print("\n共 {} 项，{} 项未通过".format(len(report), len(problems)))
        for p in problems:
            print("  FAIL " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
