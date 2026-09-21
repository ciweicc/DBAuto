#!/usr/bin/env python3
"""搜索结果的「文件清单」展开走查（issue #88：「搜出来点进去不能看集数全不全」）。

用户诉求原文：一句话 ——「搜索资源的功能搜出来点进去不能看集数全不全」，
第三轮澄清成「不需要显示集数……只需要显示这条链接里面的具体文件内容层级
就可以了，点文件按钮显示里面有什么文件，我自己判断多少集」。
搜索结果那一条过去只有「标题 / 来源 / 链接是否有效」，想确认是不是全集
只能先转存再去媒体库翻。现在每条结果多了一个「文件」按钮，点开就是
该分享的**原始文件清单**（目录 / 文件名 / 体积）。

这条链路能不能用，只能在渲染后量：
  - 静态扫描能证明「代码里有 toggleShareDetail」，但证明不了
    「点下去面板真的展开、请求真的发出去了、清单/摘要真的渲染出来了、
    再点一下真的收起来了、收起时真的没重复打接口」。

断言清单（逻辑状态 + 屏幕状态一起看）：
  1. 每条有链接的结果都带「文件」按钮；无链接的条目不给这个入口
  2. 未展开时面板不可见（不是「藏了一半」）
  3. 点击 → 面板展开、按钮变成「收起」、aria-expanded=true
  4. 结果行不再显示任何集数推断（用户第三轮明确否掉；它也必须不占请求）
  5. 请求 URL 带了 url 参数（不带就是拿不到清单的假请求）
  6. 载入中先给反馈（不能是空白面板）
  7. 渲染出文件行（名称 + 体积/目录）+ 条目数摘要 + 截断提示
  8. 失败态带真实原因（不能静默空白）
  9. 再次点击收起；按钮文案/aria 回到初始
 10. 二次展开命中缓存：不再发第二个请求
 11. 展开后下拉高度不溢出窗口（面板不能把结果顶出视口）
 12. 链接检测走**一次批量请求**（逐条检测＝N 个请求，就是「慢」的来源）

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
    {"title": "某剧 第二季", "url": "https://pan.quark.cn/s/preview-ok-2",
     "source": "夸克网盘"},
    {"title": "无链接结果", "url": "", "source": "夸克网盘"},
]}

# 批量链接检测桩：一次请求回全部链接（issue #88 第三轮「搜索速度实在有点慢」）
def _batch_stub(urls):
    link_bad = "https://pan.quark.cn/s/preview-ok-2"
    out = {}
    for u in urls:
        if u == link_bad:
            out[u] = {"valid": False, "checked": True, "message": "链接失效"}
        else:
            out[u] = {"valid": True, "checked": True, "message": "链接正常"}
    return {"success": True, "results": out}

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
    names: qa('.search-detail-name').length
      ? Array.from(qa('.search-detail-name')).map(n => n.textContent.trim()) : [],
    summary: q('.search-detail-sum') ? q('.search-detail-sum').textContent.trim() : '',
    epNodes: qa('.search-item-ep').length,
    badges: qa('.search-item-main').length
      ? Array.from(qa('.search-item-main')).map(m => m.textContent.trim()) : [],
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
        elif path == "/api/check_links":
            state["check_batch"] += 1
            urls = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("urls", [""])[0]
            state["check_urls"] += [u for u in urls.split(",") if u]
            body = _batch_stub([u for u in urls.split(",") if u])
        elif path == "/api/share_detail":
            state["detail"] += 1
            state["detail_urls"].append(route.request.url)
            body = FAIL_STUB if state.get("force_fail") else DETAIL_STUB
        elif path == "/api/check_link":
            state["check_single"] += 1
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

    state = {"search": 0, "detail": 0, "detail_urls": [], "force_fail": False,
             "check_batch": 0, "check_single": 0, "check_urls": []}
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
               st["toggleCount"] == 2,
               "toggleCount = {}（应恰好 2 个：第三条无链接）".format(st["toggleCount"]))
            ok("未展开时面板不可见", st["panelDisplay"] == "none",
               "panelDisplay = {!r}".format(st["panelDisplay"]))
            # 结果行**不再**有集数概览（issue #88 第三轮：用户明确不要集数）。
            ok("结果行不再出现集数推断（用户第三轮明确否掉）",
               st["epNodes"] == 0, "epNodes = {}".format(st["epNodes"]))
            # 标题里出现「集」是资源名本身，不算推断；这里钉的是**结果行多出来的
            # 那句概览文案**（过去是「共 22 集 · 1-22 · 缺 8、9」）已经不存在。
            ok("结果行不再出现集数断言文案（共 N 集 / 缺 N）",
               not any("共 " in b and "集" in b for b in st["badges"])
               and not any("缺 " in b for b in st["badges"]),
               "badges = {}".format([b[:44] for b in st["badges"]][:3]))
            # 链接检测必须是**一次**批量请求（逐条＝N 个请求，正是「慢」的来源）
            ok("链接检测走一次批量请求（不再是逐条）",
               state["check_batch"] == 1 and state["check_single"] == 0,
               "batch={} single={}".format(state["check_batch"], state["check_single"]))
            ok("批量请求带上了全部有链接的结果（不漏检）",
               sorted(state["check_urls"]) == ["https://pan.quark.cn/s/preview-ok",
                                               "https://pan.quark.cn/s/preview-ok-2"],
               "urls = {}".format(state["check_urls"]))
            ok("批量检测结果被渲染到对应结果行（失效的标红并禁用转存）",
               any("链接失效" in b for b in st["badges"])
               and any("链接正常" in b for b in st["badges"]),
               "badges = {}".format([b[-12:] for b in st["badges"]][:3]))
            # 文件清单仍会预热（展开即用），但它是【并行】的，不阻塞检测渲染；
            # 有链接的结果各预热一次，展开本身不再新增请求。
            ok("展开前已预热两条结果的清单（懒加载仍成立）",
               state["detail"] == 2,
               "detail calls = {}（应为 2：两条有链接的结果各预热一次）".format(state["detail"]))
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
            ok("展开本身不再新增请求（清单已预热）", state["detail"] == 2,
               "detail calls = {}（应为预热的 2 次，展开不多打）".format(state["detail"]))
            ok("请求 URL 带上了分享链接（不带＝拿不到清单）",
               state["detail_urls"] and "url=" in state["detail_urls"][0],
               "url = {}".format(state["detail_urls"][:1]))

            ok("渲染出文件行", st["rows"] == 11,
               "rows = {}（桩里有 1 目录 + 10 视频）".format(st["rows"]))
            ok("目录行被标出（可区分目录/文件）", st["dirRows"] == 1,
               "dirRows = {}".format(st["dirRows"]))
            ok("每行有体积/类型单元格", st["sizeCells"] == st["rows"],
               "sizeCells={} rows={}".format(st["sizeCells"], st["rows"]))
            ok("文件行保留了完整文件名（用户自己判断集数）",
               st["names"] and any(n.endswith(".mkv") for n in st["names"]),
               "names = {}".format(st["names"][:3]))
            ok("摘要只报条目数/目录数/视频数（不做集数推断）",
               "条目" in st["summary"] and "集" not in st["summary"],
               "summary = {!r}".format(st["summary"]))
            ok("摘要字号已提到 12px（旧版 11px）",
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
            ok("二次展开不再发请求（前端缓存）", state["detail"] == 2,
               "detail calls = {}（应仍为 2：只有预热的那两次）".format(state["detail"]))
            page.close()

            # ---------------- 失败态 ----------------
            state_fail = {"search": 0, "detail": 0, "detail_urls": [], "force_fail": True,
                          "check_batch": 0, "check_single": 0, "check_urls": []}
            page = open_page(state_fail)
            search(page)
            stf = ms(page)
            ok("失败态下结果行不出现集数推断（依旧不猜）",
               stf["epNodes"] == 0, "epNodes = {}".format(stf["epNodes"]))
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
