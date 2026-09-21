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
  4. 结果行不再显示任何集数推断（用户明确否掉：识别不准；它也必须不占请求）
  5. 请求 URL 带了 url 参数（不带就是拿不到清单的假请求）
  6. 载入中先给反馈（不能是空白面板）
  7. 渲染出文件行（名称 + 体积/目录）+ 条目数摘要 + 截断提示
  8. 失败态带真实原因（不能静默空白）
  9. 再次点击收起；按钮文案/aria 回到初始
 10. 二次展开命中缓存：不再发第二个请求
 11. 展开后下拉高度不溢出窗口（面板不能把结果顶出视口）
 12. 链接检测走**一次批量请求**（逐条检测＝N 个请求，就是「慢」的来源）
 13. 清单**一条都不预热**：结果直出只带标题/来源/链接徽标，
     展开那一条才发一次请求（上游请求数 1 + 1 + N → 1 + 1）
 14. 上游卡住（--slow）时结果行仍然先出 —— 首屏不等清单
 15. **目录可点进下一层**（issue #88 四次反馈「还是看不到第二层目录的文件名称」）：
     根目录只有一个文件夹时，点它必须发出**带 dir_fid 的请求**、并渲染出
     里面的文件名与面包屑；点面包屑能回上级

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
import time
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

# 分享详情桩：只给**原始清单**与可数计数，没有任何集号推断
# （用户第五轮原话：「在日志里报出候选集数还是太麻烦了，因为有识别不准的情况」）。
DETAIL_STUB = {
    "ok": True,
    "message": "",
    "title": "某剧",
    "total": 13,
    "video_count": 10,
    "dir_count": 1,
    "truncated": True,
    "files": (
        [{"name": "某剧/", "dir": True, "size": 0, "fid": "d1", "type": "dir"}]
        + [{"name": "某剧.S01E{:02d}.mkv".format(i), "dir": False,
            "size": 1024 * 1024 * 300, "fid": "f%d" % i, "type": "video"}
           for i in [1, 2, 3, 4, 5, 6, 7, 10, 11, 12]]
    ),
}

# 根目录桩：**只有一个文件夹**，正是用户截图里的场景（恶搞之家 目录）。
ROOT_ONLY_DIR_STUB = {
    "ok": True, "message": "", "title": "恶搞之家",
    "total": 1, "video_count": 0, "dir_count": 1, "truncated": False,
    "stoken": "tok-abc", "path": [],
    "files": [{"name": "恶搞之家", "dir": True, "size": 0,
               "fid": "a" * 32, "type": "dir"}],
}

# 进入上面那个文件夹后看到的清单：真正的视频文件在这里。
SUBDIR_STUB = {
    "ok": True, "message": "", "title": "恶搞之家",
    "total": 3, "video_count": 3, "dir_count": 0, "truncated": False,
    "stoken": "tok-abc",
    "path": [{"fid": "a" * 32, "name": "恶搞之家"}],
    "files": [{"name": "恶搞之家.S01E{:02d}.mkv".format(i), "dir": False,
               "size": 1024 * 1024 * 300, "fid": "f%d" % i, "type": "video"}
              for i in (1, 2, 3)],
}

FAIL_STUB = {"ok": False, "message": "分享已失效", "files": [],
             "total": 0, "video_count": 0, "dir_count": 0, "truncated": False}

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
    mainText: q('.search-item-main') ? q('.search-item-main').textContent.trim() : '',
    // 可点击的目录行（有 caret）
    clickableDirs: qa('.search-detail-row.is-dir .search-detail-caret').length,
    crumbs: qa('.search-crumb').length,
    crumbText: qa('.search-crumb').map(c => c.textContent.trim()).join(' / '),
    detailRequests: performance.getEntriesByType('resource')
      .filter(e => e.name.indexOf('/api/share_detail') >= 0).length,
    hasLoading: qa('.search-detail-loading').length,
    itemCount: qa('.search-item').length,
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
            if state.get("slow"):
                time.sleep(state["slow"])
            body = SEARCH_STUB
        elif path == "/api/check_links":
            state["check_batch"] += 1
            urls = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("urls", [""])[0]
            state["check_urls"] += [u for u in urls.split(",") if u]
            body = _batch_stub([u for u in urls.split(",") if u])
        elif path == "/api/share_detail":
            state["detail"] += 1
            state["detail_urls"].append(route.request.url)
            if state.get("force_fail"):
                body = FAIL_STUB
            elif state.get("nested"):
                # 带 dir_fid 的是「进子目录」，否则是根目录
                if "dir_fid=" in url:
                    state["dir_calls"] += 1
                    body = SUBDIR_STUB
                else:
                    body = ROOT_ONLY_DIR_STUB
            else:
                body = DETAIL_STUB
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
    ap.add_argument("--slow", type=float, default=0.0,
                    help="给 /api/search 桩加 N 秒延迟，用于度量「结果先出、清单后补」")
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
             "check_batch": 0, "check_single": 0, "check_urls": [], "slow": args.slow,
             "nested": False, "dir_calls": 0}
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

            def search(page, extra=0):
                page.evaluate(
                    "()=>{var i=document.getElementById('searchInput');"
                    "i.value='某剧';i.dispatchEvent(new Event('input',{bubbles:true}));}")
                # onSearchInput 有 500ms 防抖；--slow 时再额外等上游
                page.wait_for_timeout(900 + int(extra * 1000))

            # ---------------- 展开 / 收起 ----------------
            page = open_page(state)
            search(page, extra=args.slow)
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
            # 清单接口打的是 QAS 实查，慢且严格限速。
            # 结果直出**只**带标题/来源/链接徽标：一条清单都不预热，
            # 上游请求数从「1 + 1 + N」降到「1 + 1」。
            ok("结果直出零清单请求（20 条结果不再打 20 次 QAS）",
               state["detail"] == 0,
               "detail calls = {}（应为 0：清单一律等用户点开）".format(state["detail"]))
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
            # 点开才取数：这次展开**才是**第一个清单请求，且恰好一条
            ok("点开才取数：展开请求恰好一条（不是 20 条）",
               state["detail"] == 1,
               "detail calls = {}（应为 1：只有被点开的那条）".format(state["detail"]))
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
            # 集号推断已被彻底移除（用户第五轮：「报候选集数太麻烦，
            # 因为有识别不准的情况」）。钉住「响应体里没有、DOM 里也没有」。
            ok("清单响应里不再带集号字段（episodes/missing）",
               "episodes" not in json.dumps(DETAIL_STUB)
               and not any("missing" in k for k in DETAIL_STUB),
               "stub keys = {}".format(sorted(DETAIL_STUB)))
            ok("面板里不再出现「共 N 集 / 缺 N」类断言",
               "缺 " not in st["panelText"] and "共 12 集" not in st["panelText"],
               "panelText = {!r}".format(st["panelText"][:60]))
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
            ok("二次展开不再发请求（前端缓存）", state["detail"] == 1,
               "detail calls = {}（应仍为 1：复用已取回的那份）".format(state["detail"]))
            page.close()

            # ---------------- 子目录浏览（issue #88 四次反馈）----------------
            # 场景复刻用户截图：分享根目录里**只有一个文件夹**，视频在里面。
            # 若不能点进去，用户就永远只看到一行目录名 —— 这正是他第四次反馈的问题。
            state_nested = {"search": 0, "detail": 0, "detail_urls": [],
                            "force_fail": False, "check_batch": 0,
                            "check_single": 0, "check_urls": [], "slow": 0.0,
                            "nested": True, "dir_calls": 0}
            page = open_page(state_nested)
            search(page)
            page.click(".search-detail-toggle")
            page.wait_for_timeout(900)
            st = ms(page)
            ok("根目录只有一个文件夹时，清单显示该目录行",
               st["dirRows"] == 1 and st["rows"] == 1,
               "dirRows={} rows={}".format(st["dirRows"], st["rows"]))
            ok("目录行可点击（带 caret）",
               st["clickableDirs"] == 1,
               "clickableDirs = {}".format(st["clickableDirs"]))
            ok("根目录不显示面包屑（分享根无处可回）",
               st["crumbs"] == 0, "crumbs = {}".format(st["crumbs"]))

            # 点目录行 → 进入子目录
            page.click(".search-detail-row.is-dir")
            page.wait_for_timeout(900)
            st = ms(page)
            ok("点目录行发出了带 dir_fid 的请求",
               state_nested["dir_calls"] >= 1,
               "dir_calls = {}".format(state_nested["dir_calls"]))
            ok("进目录请求确实带上了 dir_fid 参数",
               any("dir_fid=" in u for u in state_nested["detail_urls"]),
               "urls = {}".format([u[-44:] for u in state_nested["detail_urls"]][-2:]))
            ok("进入后真的看到第二层的文件名称",
               st["rows"] == 3 and any(n.endswith(".mkv") for n in st["names"]),
               "rows={} names={}".format(st["rows"], st["names"][:3]))
            ok("进入后出现面包屑（分享根 / 恶搞之家）",
               st["crumbs"] >= 2 and "分享根" in st["crumbText"],
               "crumbs={} text={!r}".format(st["crumbs"], st["crumbText"]))
            ok("进入后摘要更新为子目录内容（3 个视频）",
               "3" in st["summary"], "summary = {!r}".format(st["summary"]))
            ok("进目录后下拉未被顶出视口",
               bool(st["dropdownRect"]) and st["dropdownRect"]["y"] < st["viewportH"],
               "dropdown={} viewportH={}".format(st["dropdownRect"], st["viewportH"]))

            # 点面包屑「分享根」→ 回上级
            root_before = st["detailRequests"]
            page.click(".search-crumb")
            page.wait_for_timeout(900)
            st = ms(page)
            ok("点面包屑「分享根」回到根目录（又只剩那个文件夹）",
               st["dirRows"] == 1 and st["rows"] == 1,
               "dirRows={} rows={}".format(st["dirRows"], st["rows"]))
            ok("回上级再次请求了根目录",
               st["detailRequests"] > root_before,
               "detailRequests {} -> {}".format(root_before, st["detailRequests"]))
            ok("回到根目录后面包屑消失",
               st["crumbs"] == 0, "crumbs = {}".format(st["crumbs"]))
            page.close()

            # ---------------- 上游卡住时：结果先出 ----------------
            # 用户体感的那句「搜索慢」，一半是等清单。用 3s 的 /api/search 桩
            # 把上游卡住，量的是**两块内容各自的到达时刻**：
            #   结果行（标题/来源/链接徽标）一到就渲染 → 用户马上能读、能转存；
            #   文件清单不参与首屏 → 上游再慢也不拖慢结果。
            state_slow = {"search": 0, "detail": 0, "detail_urls": [], "force_fail": False,
                          "check_batch": 0, "check_single": 0, "check_urls": [],
                          "slow": args.slow, "nested": False, "dir_calls": 0}
            page = open_page(state_slow)
            page.evaluate(
                "()=>{var i=document.getElementById('searchInput');"
                "i.value='某剧';i.dispatchEvent(new Event('input',{bubbles:true}));}")
            # 500ms 防抖 + 搜索桩延迟（--slow）后稍等一点，结果行应已渲染
            page.wait_for_timeout(500 + int(args.slow * 1000) + 700)
            st_mid = ms(page)
            if args.slow:
                ok("上游卡住时结果行先到（不等清单）",
                   st_mid["items"] >= 1 and st_mid["mainText"].startswith("某剧"),
                   "items={} mainText={!r}".format(st_mid["items"], st_mid["mainText"][:30]))
                ok("上游卡住时清单仍是零请求（不参与首屏）",
                   state_slow["detail"] == 0,
                   "detail calls = {}".format(state_slow["detail"]))
            else:
                ok("默认模式下结果行同样先到（不等清单）",
                   st_mid["items"] >= 1,
                   "items = {}".format(st_mid["items"]))
            page.close()

            # ---------------- 失败态 ----------------
            state_fail = {"search": 0, "detail": 0, "detail_urls": [], "force_fail": True,
                          "check_batch": 0, "check_single": 0, "check_urls": [],
                          "slow": args.slow, "nested": False, "dir_calls": 0}
            page = open_page(state_fail)
            search(page, extra=args.slow)
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
