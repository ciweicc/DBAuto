#!/usr/bin/env python3
"""概览页 DOM 度量走查（UI 路线图第十节 / issue #8）。

用途：
  在真实浏览器里加载构建产物，注入确定性的接口桩数据，
  对概览页首屏的**可量化缺陷**做断言 —— 这些指标用静态扫描
  （tests/test_build.py）表达不了，只能在渲染后测量：

  1. 首屏工作区必须整体落在一屏内（**首屏工作区** = 概览指标 + 最近转存 + 服务状态/待办，
     不含下方的 TMDB 发现分区；issue #14 之后发现区常驻概览页尾部，
     要求整页一屏会把筛选栏/海报墙直接判成溢出）。度量口径：
     取「发现区之前所有 #pageOverview > * 块」的最大底边作为首屏折叠线。
  2. 指标行等高且副信息不被裁切（曾出现 81/93 参差 + 「失败 86」被挤断）；
  3. 最近转存表不得出现横向滚动条；
  4. 面板内不得出现纵向滚动容器（概览整体滚动交给 .content）；
  5. 跳过状态必须与成功状态有区分（不得全部渲染为「已转存」）；
  6. 空日期条目必须排在列表末尾，且日期格式为 MM/DD HH:MM；
  7. 移动端表格卡片化后，分类前缀不得被压成竖排单字；
  8. 横向溢出（文档 + 内容区）必须为 0；
  9. 待办里的「去 TMDB 发现」必须真的把发现区带到眼前（issue #24）。
  10.「最近转存」标题列必须够宽，「常规标题」不得被省略号截断
     （issue #39 追问：「让这个模块的横向面积更大一点显示完整内容」）。

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



def _launch_kwargs():
    """本机 playwright 版本与已装 chromium 目录不匹配时回退到显式可执行文件。

    仅影响开发环境的手工走查（CI 上通常直接跳过），不改变断言内容。
    """
    import glob
    for cand in sorted(glob.glob("/opt/ms-playwright/chromium-*/chrome-linux64/chrome")):
        if os.path.isfile(cand):
            return {"executable_path": cand}
    return {}


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

# 首屏两列允许的纯空白上限（issue #15 第 4 轮）：
# 卡片/块的底部内边距约 12~14px，留到 24px 作为余量。
# 修复前实测：左列 279px、待办块内 291px。
MAX_HERO_BLANK = 24

MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const content = q('.content');
  // 折叠线必须与「当前滚动位置」无关：先把滚动容器归零再测量。
  // （issue #14 之后发现区常驻概览页底部，页面首屏之后还有内容，
  //  若沿用「任何与视口相交的块」这种相对判定，滚动位置一变结论就变。）
  content.scrollTop = 0;
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
    // 首屏工作区折叠线：最后一个「顶边仍在视口内」的块。
    // issue #14 之后发现区（TMDB）常驻概览页底部：它是「滚下去用」的工作台，
    // 只要它整个在折叠线以下就不算首屏内容；若它已探进视口（顶边可见），
    // 说明首屏已经把用户带到了发现区，此时它的底边才是真正的首屏下沿。
    lastBlockBottom: Math.round((() => {
      const sr = content.getBoundingClientRect();
      let bottom = 0;
      [...document.querySelectorAll('#pageOverview > *')].forEach(e => {
        const r = e.getBoundingClientRect();
        if (r.top > sr.bottom) return;       // 完全在视口之下：属于下一页内容
        if (r.bottom > bottom) bottom = r.bottom;
      });
      return bottom;
    })()),
    // 首屏工作区下沿 = 发现区之前所有块的底边（发现区本身不算首屏，见下方校验注释）
    screenBottom: Math.round((() => {
      const host = q('#tmdbPanel');
      let bottom = 0;
      [...document.querySelectorAll('#pageOverview > *')].forEach(e => {
        if (host && (e === host || e.contains(host))) return;
        const b = e.getBoundingClientRect().bottom;
        if (b > bottom) bottom = b;
      });
      return bottom;
    })()),
    statRowH: rows.map(r => Math.round(r.getBoundingClientRect().height)),
    statRowClipped: rows.filter(r => r.querySelector('.ov-stat-line') &&
      r.querySelector('.ov-stat-line').scrollWidth > r.querySelector('.ov-stat-line').clientWidth + 1).length,
    tableOverflowX: (() => { const w = q('.ov-table-wrap'); return w ? w.scrollWidth - w.clientWidth : 0; })(),
    scrollers,
    recentRows: recRows.length,
    // issue #39：恒常列折叠。桩数据里 status 恒非空（→「库内」恒为「是」），
    // shareurl 每 4 条留一条为空（→「链接」列仍有区分度）。
    // 因此期望：库内列折叠（表头 hidden 且 tbody 无该 td），链接列保留。
    // 同时断言表头与表体的列数一致 —— 折叠只改可见性不改结构，
    // 若两边判据漂移，这里会直接暴露列错位。
    recentCols: {
      headTotal: document.querySelectorAll('.ov-table thead th').length,
      headVisible: [...document.querySelectorAll('.ov-table thead th')]
        .filter(e => !e.hasAttribute('hidden')).length,
      bodyCells: recRows.length ? recRows[0].querySelectorAll('td').length : 0,
      libHidden: (() => { const t = q('.ov-table thead th[data-ov-col="lib"]');
        return t ? t.hasAttribute('hidden') : null; })(),
      linkHidden: (() => { const t = q('.ov-table thead th[data-ov-col="link"]');
        return t ? t.hasAttribute('hidden') : null; })(),
      libCell: !!q('#ovRecentBody td.ov-table-lib'),
      linkCell: !!q('#ovRecentBody td.ov-table-actions'),
      sub: (() => { const e = q('#ovRecentSub'); return e ? e.textContent : null; })(),
    },
    // issue #39 追问：标题列必须拿到足够宽度，「显示完整内容」不能被省略号截断。
    // 判据用 scrollWidth > clientWidth（而不是"看有没有 …"）：
    // 省略号是 CSS 渲染的，DOM 里读不到，只有溢出量是确定性的文本证据。
    recentTitle: {
      // 表头里带 width 声明的列（时间/状态/链接）与标题列各自的实测宽度
      fixedColW: [...q('.ov-panel-recent .ov-table').querySelectorAll('thead th')]
        .filter(th => !th.hasAttribute('hidden') && th.textContent.trim() !== '标题')
        .reduce((sum, th) => sum + Math.round(th.getBoundingClientRect().width), 0),
      titleColW: (() => { const th = [...q('.ov-panel-recent .ov-table').querySelectorAll('thead th')]
        .find(t => t.textContent.trim() === '标题');
        return th ? Math.round(th.getBoundingClientRect().width) : 0; })(),
      // ≤640px 时表格转「每行一张卡」（thead display:none、td display:flex），
      // 此时表头宽度无意义（恒 0），列宽断言不适用。
      cardLayout: getComputedStyle(q('.ov-panel-recent .ov-table thead')).display === 'none',
      rows: [...document.querySelectorAll('#ovRecentBody .ov-table-title')].map(td => ({
        clipped: td.scrollWidth > td.clientWidth + 1,
        need: td.scrollWidth,
        got: td.clientWidth,
        text: td.textContent.trim(),
      })),
    },
    statuses: [...new Set([...document.querySelectorAll('#ovRecentBody .ov-badge')].map(e => e.textContent))],
    firstDate: (() => { const e = q('#ovRecentBody .ov-table-date'); return e ? e.textContent : null; })(),
    lastDate: (() => { const all = document.querySelectorAll('#ovRecentBody .ov-table-date');
      return all.length ? all[all.length - 1].textContent : null; })(),
    lastDateText: (() => { const all = [...document.querySelectorAll('#ovRecentBody tr')];
      return all.length ? all[all.length - 1].textContent : null; })(),
    catW: cat ? Math.round(cat.getBoundingClientRect().width) : null,
    // issue #15 第 4 轮：首屏两列的「纯空白」度量。
    // 背景：「最近转存」固定 10 行（≈480px）会把整行拉到同高，而左列
    // 「转存概览」内容只有约 210px，差额全部变成左侧死白（实测 279px）。
    // leftBlank：左列底部（指标行末行底边 → 面板底边）；
    // todoBlank：右列「待办事项」块内，列表底边 → 块底边。
    leftBlank: Math.round((() => {
      // 只在「指标 | 状态」并排时度量：此时面板高度由右列（待办）决定，
      // 若面板比指标列高，差额就是死白。
      // 堆叠态（.app[data-ov-narrow="1"]）下面板下方本来就是状态/待办块，
      // 「指标列底边 → 面板底边」自然很大，但那不是空白，故跳过。
      if (document.querySelector('.app[data-ov-narrow="1"]')) return 0;
      const panel = q('.ov-overview');
      const rowsAll = document.querySelectorAll('.ov-stat-row');
      if (!panel || !rowsAll.length) return 0;
      const last = rowsAll[rowsAll.length - 1].getBoundingClientRect().bottom;
      return Math.max(0, panel.getBoundingClientRect().bottom - last);
    })()),
    todoBlank: Math.round((() => {
      if (document.querySelector('.app[data-ov-narrow="1"]')) return 0;  // 同上：堆叠态不适用
      const block = q('.ov-side-todo');
      const list = q('#ovTodoList');
      if (!block || !list) return 0;
      return Math.max(0, block.getBoundingClientRect().bottom - list.getBoundingClientRect().bottom);
    })()),
    catH: cat ? Math.round(cat.getBoundingClientRect().height) : null,
    // issue #15：首屏上方的「发现快捷」横滑条与下方发现区内容重复，已整栏移除。
    // 这里断言「同一批热门数据只有一份渲染」，防复发（比静态扫描更接近真实 DOM）。
    quickPanel: !!q('#pageOverview .ov-panel-rec, #ovRecScroll'),
    discoveryInOverview: !!(q('#pageOverview #tmdbPanel')),
    discoveryFilters: !!q('#pageOverview #tmdbFilters .tmdb-filter-group'),
    // issue #24：待办里的「去 TMDB 发现」点完必须真的把发现区带到眼前。
    // 这里只读「有没有那条待办 + 按钮接线」，位移判定在下面的 discoverJump 里做。
    todoDiscoveryBtn: (() => {
      const b = q('#ovTodoList .ov-todo-btn');
      return b ? (b.getAttribute('onclick') || '') : null;
    })(),
    legacyTmdbPage: !!q('#pageTmdb'),
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


_JUMP_PROBE = r"""
() => {
  const sc = document.querySelector('.content');
  const host = document.querySelector('#tmdbPanel');
  if (!sc || !host) return null;
  const btn = document.querySelector('#ovTodoList .ov-todo-btn');
  if (!btn) return null;
  const before = Math.round(sc.scrollTop);
  btn.click();
  return { before, beforeTop: Math.round(host.getBoundingClientRect().top) };
}
"""


def _probe_discovery_jump(page):
    """点一次待办里的「去 TMDB 发现」，读前后滚动位置与发现区顶边是否进入视口。

    发现区滚到位后是 `top ≈ 内容区顶 + 12px`（scrollTmdbPanelIntoView 的留白），
    因此判定「顶边进入视口」用「比内容区顶边低、且不超过内容区高度的一半」，
    对 9 组视口都成立（移动端内容区本身就矮）。
    """
    out = {"before": 0, "after": 0, "beforeTop": 0, "afterTop": 0, "reachable": False}
    got = page.evaluate(_JUMP_PROBE)
    if not got:
        return out
    out.update(got)
    page.wait_for_timeout(700)          # 平滑滚动 (behavior:'smooth')
    after = page.evaluate(
        """() => {
            const sc = document.querySelector('.content');
            const host = document.querySelector('#tmdbPanel');
            const sr = sc.getBoundingClientRect();
            const hr = host.getBoundingClientRect();
            return {top: Math.round(sc.scrollTop),
                    rel: Math.round(hr.top - sr.top),
                    scH: Math.round(sr.height)};
        }"""
    )
    out["after"] = after["top"]
    out["afterTop"] = after["rel"]
    out["reachable"] = after["scH"] <= 0 or (0 < after["rel"] < after["scH"] / 2)
    return out


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
            browser = p.chromium.launch(args=["--no-sandbox"], **_launch_kwargs())
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
                # issue #39 追问：标题列必须够宽，常规标题不得被省略号截断。
                # 桩数据最长标题为「剧集示例作品标题-00」（含分类前缀约 157px），
                # 这就是「常规标题」的下限口径：连它都截断，说明列宽分配又退化了。
                t = m["recentTitle"]
                clipped = [r for r in t["rows"] if r["clipped"]]
                if clipped:
                    fail("「最近转存」标题列过窄，{} 行被截断（列宽 {}px，最长需 {}px）：{}"
                         .format(len(clipped), t["titleColW"], max(r["need"] for r in t["rows"]),
                                 clipped[0]["text"]))
                if not t["cardLayout"] and m["recentRows"] and t["titleColW"] < 150:
                    fail("「最近转存」标题列宽 {}px < 150px（列宽被定宽列挤占）"
                         .format(t["titleColW"]))
                # 定宽列不得反向膨胀（时间/状态/链接是按内容钉死的，不该抢标题列）
                if not t["cardLayout"] and m["recentRows"] and t["fixedColW"] > 240:
                    fail("「最近转存」定宽列合计 {}px > 240px（挤占标题列宽度）"
                         .format(t["fixedColW"]))
                # issue #39：恒常列折叠（桩数据下 status 恒非空 → 库内列应折叠）
                c = m["recentCols"]
                if c["libHidden"] is not True or c["libCell"]:
                    fail("「库内」列是常量列（全为「是」）却未被折叠："
                         "hidden={} cell={}".format(c["libHidden"], c["libCell"]))
                if c["linkHidden"] is not False or not c["linkCell"]:
                    fail("「链接」列仍有区分度（部分行无链接）却被折叠："
                         "hidden={} cell={}".format(c["linkHidden"], c["linkCell"]))
                if c["headVisible"] != c["bodyCells"] + 0 and m["recentRows"]:
                    fail("表头可见列数 {} 与表体单元格数 {} 不一致（折叠后列错位）"
                         .format(c["headVisible"], c["bodyCells"]))
                if c["sub"] is None or "均在库内" not in (c["sub"] or ""):
                    fail("折叠掉「库内」列后，结论未并进面板副标题：{}".format(c["sub"]))
                # 概览页内不得再有指向历史页的入口（issue #39）
                if page.evaluate(
                        "() => [...document.querySelectorAll('#pageOverview *')]"
                        ".filter(e => (e.getAttribute('onclick') || '').includes(\"switchTab('history')\")).length"
                ):
                    fail("概览页内仍有指向历史页的入口")
                if m["quickPanel"]:
                    fail("「发现快捷」横滑条仍存在（与下方发现区内容重复）")
                # 首屏工作区（概览指标 + 最近转存 + 服务状态/待办）必须落在一屏内。
                # 注：这里不能再断言「#pageOverview 的最后一个块的底边」——
                # issue #14 之后发现区（TMDB 工作台）常驻概览页底部，它是滚下去用的，
                # 把它算进首屏会让断言恒为失败（用户明确要的就是「同页承载」）。
                # 首屏折叠线 = 发现区之前那些块的底边（见 MEASURE.screenBottom）。
                if on_screen and m["screenBottom"] > height:
                    fail("首屏工作区超出视口：底边 {} > {}".format(m["screenBottom"], height))
                if "ov-table-wrap" in m["scrollers"]:
                    fail("最近转存仍在面板内滚动（高度与左侧概览脱钩）")
                # issue #15 第 4 轮：首屏两列不得出现整块死白。
                # 阈值 24px：指标行/待办块自身的内边距约 12~14px，
                # 留一倍余量即可拦住「被邻居按同高撑开」这类回归
                # （修复前左列空 279px、待办块内空 291px）。
                if m["leftBlank"] > MAX_HERO_BLANK:
                    fail("左列底部纯空白 {}px（上限 {}px）".format(m["leftBlank"], MAX_HERO_BLANK))
                if m["todoBlank"] > MAX_HERO_BLANK:
                    fail("「待办事项」块内纯空白 {}px（上限 {}px）".format(m["todoBlank"], MAX_HERO_BLANK))
                if not {"已转存", "已存在跳过"} <= set(m["statuses"]):
                    fail("跳过与成功未区分：{}".format(m["statuses"]))
                if m["firstDate"] and m["firstDate"].count("/") != 1:
                    fail("日期格式不是 MM/DD HH:MM：{}".format(m["firstDate"]))
                if m["lastDateText"] is not None and "无日期条目" in (m["lastDateText"] or ""):
                    fail("空日期条目未排在列表末尾")
                if width <= 640 and m["catW"] is not None and m["catW"] < 24:
                    fail("移动端分类前缀被压成竖排单字（宽 {}px）".format(m["catW"]))
                # issue #24：待办「去 TMDB 发现」必须真的能跳到发现区。
                # 桩数据里 today_count=3（有今日转存），因此「今日还没有转存记录」
                # 这条待办不会出现 —— 不是每个视口都能断言到按钮，先记进报告。
                if m["todoDiscoveryBtn"] is not None:
                    jump = _probe_discovery_jump(page)
                    m["discoveryJump"] = jump
                    if jump["after"] <= jump["before"] + 8:
                        fail("点「去 TMDB 发现」后没有位移：scrollTop {} → {}，"
                             "发现区顶边相对视口 {} → {}"
                             .format(jump["before"], jump["after"],
                                     jump["beforeTop"], jump["afterTop"]))
                    if not jump["reachable"]:
                        fail("点「去 TMDB 发现」后发现区顶边未进入视口（相对内容区顶 {}px）"
                             .format(jump["afterTop"]))
                # issue #14：TMDB 的发现区必须住在概览页里（不再有第二个页面），
                # 且筛选栏存在 —— 「原版筛选栏移植到概览页」正是这次的诉求本身。
                if not m["discoveryInOverview"]:
                    fail("发现区（#tmdbPanel）未落在 #pageOverview 内")
                if not m["discoveryFilters"]:
                    fail("概览页内没有渲染出 TMDB 筛选栏（#tmdbFilters）")
                page.close()
            browser.close()
    finally:
        if httpd is not None:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report}, ensure_ascii=False, indent=2))
    else:
        for m in report:
            print("{:<26} 指标行高 {:<16} 表格溢出 {:<3} 首屏底边 {:<6} 标题列 {:<4} 发现区 {} 状态 {}".format(
                m["label"], str(m["statRowH"]), m["tableOverflowX"], m["screenBottom"],
                "{}px".format(m["recentTitle"]["titleColW"]),
                "页内" if m["discoveryInOverview"] else "缺失",
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
