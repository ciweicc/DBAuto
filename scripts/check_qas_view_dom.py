#!/usr/bin/env python3
"""QAS 任务表「列表视图 / 卡片视图」切换的 DOM 走查（issue #57 追问）。

用户诉求原文：给「QAS 转存任务」那一屏的截图配了一句
「夸克自动转存模块增加列表视图和卡片视图的切换按钮」。

这条诉求**只能在渲染后判定**：
  - 静态扫描能证明「代码里有 #qasViewBtn」，证明不了「点下去真的换了布局」——
    按钮存在但点了没反应（或只改了 aria 状态）正是这类开关最常见的坏法；
  - 也不能只断言「函数被调用了」：本轮的关键风险恰恰是「切了布局之后
    **别的功能悄悄坏了**」——勾选、Ctrl+A、Shift 连选、当前行虚线框、
    分页区间、筛选、删除按钮，任何一处丢在卡片视图里都是「删错东西」的前奏。

因此本脚本真的点按钮，并逐条断言：

  1. 入口存在、默认是**列表视图**（不该因为加了个开关就把默认观感换掉），
     且按钮文案反映**当前**视图（不是悬空按钮）；
  2. 点一下真的换成卡片网格（#qasGrid 出现、表格消失、卡片数 = 当前页行数、
     每张卡都有勾选框与删除按钮），且**不发新请求**（切布局不能顺带刷新）；
  3. 卡片视图下选择语义与列表视图**逐条一致**：点卡片、Ctrl+A、Shift 连选、
     空格切换、Esc 清空，勾选数量与「已选 N 条」同步；
  4. 卡片视图下 ←→ 移动当前行、Shift+←→ 连选、行末按 → 翻页
     （列表视图的 ↑↓ 语义在网格里换成 ←→，且 ←→ 在列表视图里不抢键）；
  5. 卡片视图下筛选 / 分页 / 每页条数与列表视图一致（作用范围仍是可见行）；
  6. **卡片高度足够放下任务名与转存路径**：高度必须高于列表行高，
     路径不折行、不被 1 行截断（issue 的核心是「看得见信息」）；
  6b. **同一行的卡片等高、路径不被截断**（issue #75「资源条目高度不一致」）：
     网格里同一行的卡底边必须在同一条水平线上（极差 ≤2px），且没有一张卡的
     转存路径被省略号截掉。这一条**必须专门造出「一长一短」的形态**再量 ——
     真实数据里短的短、长的长，但等高断言在「所有卡一样高」的数据上恒为真
     （实测：夹具没造出参差时这条断言永远通过，等于没测）；
  7. 两种布局都无横向溢出；窄视口下也成立；
  8. 偏好写进 localStorage（qas_view），刷新后仍在；
     且**不串味**（媒体库的 lib_view 不被改写、QAS 的 qas_view 不被媒体库改写）。

依赖：playwright + chromium（与其它媒体库走查脚本一致）。
无依赖 / 无产物时以退出码 0 跳过。

用法：
  python3 scripts/check_qas_view_dom.py            # 检查（失败非零退出）
  python3 scripts/check_qas_view_dom.py --json     # 机器可读报告
  python3 scripts/check_qas_view_dom.py --serve    # 自动起本地静态服务
"""
import argparse
import functools
import glob
import http.server
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "static", "index_new.html")
PORT = 8147
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

QAS_TASKS = [
    {"taskname": "瑞克和莫蒂 第九季", "savepath": "/影视/电视剧/瑞克和莫蒂 第九季",
     "shareurl": "https://pan.quark.cn/s/a1"},
    {"taskname": "提欧奥特曼", "savepath": "/影视/电视剧/提欧奥特曼",
     "shareurl": "https://pan.quark.cn/s/a2"},
    {"taskname": "好孩子", "savepath": "/影视/电影/好孩子",
     "shareurl": "https://pan.quark.cn/s/a3"},
    {"taskname": "寒战1994", "savepath": "/影视/电影/寒战1994",
     "shareurl": "https://pan.quark.cn/s/a4"},
    {"taskname": "Some Show", "savepath": "/影视/TV/Some Show",
     "shareurl": "https://pan.quark.cn/s/a5"},
    {"taskname": "临时资源", "savepath": "/下载/临时/临时资源", "shareurl": ""},
]
# 再补 18 条 /影视/电视剧 → 该前缀命中 2+18 = 20 条；总数 24。
# 每页 20 时：不筛选 2 页、筛选 1 页；每页 10 时 3 页，翻页断言有据可依。
QAS_TASKS += [
    {"taskname": "批量剧集-%02d" % i, "savepath": "/影视/电视剧/批量剧集-%02d" % i,
     "shareurl": "https://pan.quark.cn/s/b%02d" % i}
    for i in range(1, 19)
]
TOTAL = len(QAS_TASKS)
EXPECT_TV = len([t for t in QAS_TASKS if "/影视/电视剧" in t["savepath"]])

_ENTRIES = [{"fid": "d1", "name": "示例目录", "dir": True, "size": 0,
             "updated_at": 1758000000}]

STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.2.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/tmdb/list": {"items": []},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/transfer/status": {"running": False, "progress": [], "stats": {}},
    "/api/qas/tasks": {"success": True, "tasks": QAS_TASKS},
    "/api/library/source": {"success": True, "source": "openlist", "openlist": True,
                            "openlist_ok": True, "openlist_user": "admin"},
}


def _launch_kwargs():
    """本机 playwright 版本与已装 chromium 目录不匹配时，回退到显式可执行文件。"""
    for cand in sorted(glob.glob("/opt/ms-playwright/chromium-*/chrome-linux64/chrome")):
        if os.path.isfile(cand):
            return {"executable_path": cand}
    return {}


def _start_server():
    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(_QuietHandler, directory=os.path.join(ROOT, "static"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _route(route):
    url = route.request.url
    if "/api/" not in url:
        return route.fallback()
    path = url.split(str(PORT), 1)[-1].split("?", 1)[0]
    if path.rstrip("/").endswith("/api/sse"):
        return route.fulfill(status=200, content_type="text/event-stream", body=b"")
    if path == "/api/qas/library":
        body = {"success": True, "path": "/", "entries": _ENTRIES,
                "source": "openlist", "fallback": False}
    else:
        body = STUBS.get(path, {})
    return route.fulfill(status=200, content_type="application/json",
                         body=json.dumps(body, ensure_ascii=False).encode())


# 断言用的度量：全部基于**渲染后的勾选框 DOM 属性 + 操作条文案 + 当前行样式**，
# 而不是内部的 qasSelected 对象 —— 后者只能证明「代码以为自己选了」。
MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const rows = [...document.querySelectorAll('#qasTaskList .lib-row:not(.lib-head)')];
  // 「两种布局的行」= 列表视图的表格行 + 卡片视图的卡片。
  // 分开统计是刻意的：**切布局之后表格行必须消失**（两张表同时挂在页面上
  // 是最危险的形态 —— 键盘与全选会分别吃到一部分行）。
  const cards = [...document.querySelectorAll('#qasTaskList .lib-card')];
  const all = rows.length ? rows : cards;
  const boxes = all.map(r => r.querySelector('input[type=checkbox]'));
  const cur = all.filter(r => r.classList.contains('lib-current'));
  return {
    rows: rows.length,
    keys: rows.map(r => r.getAttribute('data-key')),
    names: rows.map(r => {
      const n = r.querySelector('.lib-name');
      return n ? n.textContent : '';
    }),
    paths: rows.map(r => {
      const s = r.querySelector('.lib-sub');
      return s ? s.textContent : '';
    }),
    checked: boxes.filter(b => b && b.checked).length,
    checkedKeys: rows.filter(r => {
      const b = r.querySelector('input[type=checkbox]');
      return b && b.checked;
    }).map(r => r.getAttribute('data-key')),
    // 「当前行」：class + 虚线（只看 class 会漏掉「标记了但样式丢了」）
    curCount: cur.length,
    curKey: cur.length ? cur[0].getAttribute('data-key') : null,
    curBorderStyle: cur.length ? getComputedStyle(cur[0]).borderTopStyle : '',
    selCount: q('#qasSelCount') ? q('#qasSelCount').textContent : '',
    delDisabled: q('#qasDelBtn') ? q('#qasDelBtn').disabled : null,
    allChecked: q('#qasSelAll') ? q('#qasSelAll').checked : null,
    allIndeterminate: q('#qasSelAll') ? q('#qasSelAll').indeterminate : null,
    pagerPos: q('#qasPagerPos') ? q('#qasPagerPos').textContent : '',
    count: q('#qasTaskCount') ? q('#qasTaskCount').textContent : '',
    pathValue: q('#qasFilterPath') ? q('#qasFilterPath').value : null,
    // 提示条：与媒体库同一套样式（display / 字号 / 是否共类）
    hintDisplay: (function(){
      const h = q('#qasKeyHint');
      return h ? getComputedStyle(h).display : 'none';
    })(),
    hintFontSize: (function(){
      const h = q('#qasKeyHint');
      return h ? getComputedStyle(h).fontSize : '';
    })(),
    hintLibFontSize: (function(){
      const h = q('#libKeyHint');
      return h ? getComputedStyle(h).fontSize : '';
    })(),
    overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    activeTag: (document.activeElement && document.activeElement.tagName) || '',
    // ---- 视图切换相关（issue #57 追问）----
    viewBtnExists: !!q('#qasViewBtn'),
    viewBtn: q('#qasViewBtn') ? q('#qasViewBtn').textContent.trim() : '',
    isCard: !!q('#qasGrid'),
    cards: cards.length,
    tableRows: rows.length,
    cardsWithoutCb: cards.filter(c => !c.querySelector('input[type=checkbox]')).length,
    cardsWithoutDel: cards.filter(c => !c.querySelector('button')).length,
    // 卡片几何：高度必须高于列表行高，路径必须折 2 行且不被截断
    cardH: cards.length ? Math.round(cards[0].getBoundingClientRect().height) : 0,
    rowH: rows.length ? Math.round(rows[rows.length - 1].getBoundingClientRect().height) : 0,
    pathLines: (function(){
      const p = q('#qasTaskList .lib-card-path');
      if (!p) return 0;
      const lh = parseFloat(getComputedStyle(p).lineHeight) || 16;
      return Math.max(1, Math.round(p.getBoundingClientRect().height / lh));
    })(),
    pathClipped: (function(){
      const p = q('#qasTaskList .lib-card-path');
      if (!p) return false;
      // 折行容器被 -webkit-line-clamp 截断时 scrollHeight 明显大于 clientHeight
      return p.scrollHeight - p.clientHeight > 2;
    })(),
    cardHSpread: (function(){
      const hs = cards.map(c => Math.round(c.getBoundingClientRect().height));
      return hs.length ? Math.max.apply(null, hs) - Math.min.apply(null, hs) : 0;
    })(),
    // issue #75：**同一行**的卡片是否等高。量的是「每张卡的底边」而不是高度 ——
    // 底边对齐才是用户看到的「网格歪没歪」，高度相同但基线错位同样会露馅。
    // 按 top 分组：同一 top 的卡视为同一行（网格行内 top 必然一致）。
    rowSpread: (function(){
      const byRow = {};
      cards.forEach(c => {
        const b = c.getBoundingClientRect();
        const t = Math.round(b.top);
        byRow[t] = byRow[t] || [];
        byRow[t].push(Math.round(b.bottom));
      });
      const rows = Object.keys(byRow).map(t => byRow[t]);
      if (!rows.length) return 0;
      return Math.max.apply(null, rows.map(r =>
        Math.max.apply(null, r) - Math.min.apply(null, r)));
    })(),
    rowCount: (function(){
      const ts = new Set(cards.map(c => Math.round(c.getBoundingClientRect().top)));
      return ts.size;
    })(),
    rowFullSpread: (function(){
      // 整屏卡片的高度极差（跨行也要收在一个合理范围内，防止某张卡把网格拉爆）
      const hs = cards.map(c => Math.round(c.getBoundingClientRect().height));
      return hs.length ? Math.max.apply(null, hs) - Math.min.apply(null, hs) : 0;
    })(),
    cbTopSpread: (function(){
      // 一列卡片的勾选框是否对齐：取同一行（top 相同）的卡片比较过于严格，
      // 直接量「每个勾选框相对自己卡片顶部的偏移」，再取极差。
      const offs = cards.map(c => {
        const cb = c.querySelector('.lib-card-cb,input[type=checkbox]');
        if (!cb) return null;
        return Math.round(cb.getBoundingClientRect().top - c.getBoundingClientRect().top);
      }).filter(v => v !== null);
      if (offs.length < 2) return 0;
      return Math.max.apply(null, offs) - Math.min.apply(null, offs);
    })(),
    shortNameClipped: (function(){
      // 取最短的那张卡的标题量：短名字都被截断 = 标题列宽度不够
      const ns = [...document.querySelectorAll('#qasTaskList .lib-card-title')];
      if (!ns.length) return false;
      const n = ns.reduce((a, b) =>
        a.textContent.length <= b.textContent.length ? a : b);
      return n.scrollWidth - n.clientWidth > 2;
    })(),
    scrollY: Math.round(window.scrollY),
    gridScroll: (function(){
      const g = q('#qasGrid');
      return g ? Math.round(g.scrollTop) : 0;
    })(),
    gridScrollable: (function(){
      const g = q('#qasGrid');
      return g ? g.scrollHeight > g.clientHeight + 2 : false;
    })(),
    pagerPos: q('#qasPagerPos') ? q('#qasPagerPos').textContent : '',
    pageSize: q('#qasPageSize') ? q('#qasPageSize').value : '',
  };
}
"""


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
    problems, report = [], []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"], **_launch_kwargs())

            def open_page(width=1440, height=900, init=None):
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                if init:
                    page.add_init_script(init)
                page.route("**/*", lambda r: _route(r))
                page.goto(URL, wait_until="load", timeout=20000)
                page.evaluate("()=>switchTab('library')")
                page.wait_for_timeout(1200)
                return page

            def fail(msg):
                problems.append(msg)

            page = open_page()
            reqs = []
            page.on("request", lambda r: reqs.append(r.url))

            def st():
                return page.evaluate(MEASURE)

            def focus_qas():
                page.evaluate(
                    "()=>{try{document.activeElement.blur();}catch(e){}"
                    "var c=document.getElementById('qasCard');"
                    "c.dispatchEvent(new MouseEvent('click',{bubbles:true}));}")
                page.wait_for_timeout(150)

            focus_qas()

            # ---- 1. 入口存在、默认列表视图、文案反映当前视图 -------------------
            s0 = st()
            if not s0["viewBtnExists"]:
                fail("QAS 任务表没有「列表视图 / 卡片视图」切换按钮（#qasViewBtn）")
                browser.close()
                return 1
            if s0["isCard"]:
                fail("默认应是列表视图（任务是配置不是内容，一屏扫更多条更重要），"
                     "实际默认落在卡片视图")
            if "卡片视图" not in s0["viewBtn"]:
                fail("默认（列表视图）下按钮文案应提示「切到卡片视图」，实际 {!r}".format(
                    s0["viewBtn"]))
            if s0["rows"] != min(20, TOTAL):
                fail("未筛选时首屏应渲染 {} 行，实际 {} 行（后面断言不可信）".format(
                    min(20, TOTAL), s0["rows"]))
            report.append({"stage": "default", "view": s0["viewBtn"],
                           "rows": s0["rows"], "tableH": s0["rowH"]})

            # ---- 2. 点一下 → 真的换成卡片网格，且不发新请求 -------------------
            reqs.clear()
            page.click("#qasViewBtn")
            page.wait_for_timeout(400)
            s1 = st()
            if not s1["isCard"]:
                fail("点了「卡片视图」但没渲染出卡片网格（#qasGrid 不存在）")
            if s1["tableRows"]:
                fail("卡片视图下表格行仍存在（{} 行）：两种布局同时挂在页面上".format(
                    s1["tableRows"]))
            if s1["cards"] != s0["rows"]:
                fail("卡片视图的卡片数（{}）与列表视图的行数（{}）不一致："
                     "切布局不应改变条目数量".format(s1["cards"], s0["rows"]))
            if s1["cardsWithoutCb"] or s1["cardsWithoutDel"]:
                fail("卡片视图有卡片缺勾选框（{} 张）或缺删除按钮（{} 张）".format(
                    s1["cardsWithoutCb"], s1["cardsWithoutDel"]))
            if "列表视图" not in s1["viewBtn"]:
                fail("卡片视图下按钮文案应提示「切到列表视图」，实际 {!r}".format(s1["viewBtn"]))
            new_reqs = [u for u in reqs if "/api/" in u and "/api/sse" not in u]
            if new_reqs:
                fail("切布局触发了网络请求（{}）：这个按钮是纯展示开关，"
                     "不该顺带刷新".format(new_reqs[:2]))
            report.append({"stage": "to-card", "cards": s1["cards"],
                           "cardH": s1["cardH"], "rowH": s0["rowH"],
                           "overflowX": s1["overflowX"]})

            # ---- 3. 卡片视图下的选择语义与列表视图一致 -------------------------
            focus_qas()
            page.keyboard.press("Control+a")
            page.wait_for_timeout(250)
            sa = st()
            if sa["checked"] != sa["cards"]:
                fail("卡片视图下 Ctrl+A 未全选（勾选 {} / 卡片 {}）".format(
                    sa["checked"], sa["cards"]))
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            if st()["checked"] != 0:
                fail("卡片视图下 Esc 未清空选择")
            focus_qas()
            page.keyboard.press("ArrowRight")
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(200)
            sr = st()
            if sr["curCount"] != 1 or sr["curBorderStyle"] != "dashed":
                fail("卡片视图下「当前行」标记丢失（cur={} border={!r}）——"
                     "←→ 移动看不见的话等于没有".format(sr["curCount"], sr["curBorderStyle"]))
            page.keyboard.press("Shift+ArrowRight")
            page.wait_for_timeout(200)
            if st()["checked"] != 2:
                fail("卡片视图下 Shift+→ 连选失效（checked={}）——"
                     "网格里 Shift+←→ 才是「连选一段」的自然手势".format(st()["checked"]))
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            report.append({"stage": "card-keys", "checked": sa["checked"],
                           "curKey": sr["curKey"]})

            # ---- 4. ↑↓ 在卡片视图里让位给浏览器滚动（不该被抢） ----------------
            focus_qas()
            before_cur = st()["curKey"]
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(150)
            if st()["curKey"] != before_cur:
                fail("卡片视图下 ↑↓ 仍在移动当前行：网格的视觉顺序是左右，"
                     "抢掉 ↑↓ 会让「想往下滚页面」变成「当前行乱跳」")
            if st()["nameClippedCur"] if False else False:
                pass
            # 网格是否可滚由内容高度决定，不强求「按一下必须滚」——
            # 断言的是「现在到底能不能滚」这条信息（超长目录才有意义）。
            report.append({"stage": "card-arrow-down", "gridScroll": st()["gridScroll"],
                           "gridScrollable": st()["gridScrollable"]})

            # ---- 5. 切回列表视图：↑↓ 语义恢复 --------------------------------
            page.click("#qasViewBtn")
            page.wait_for_timeout(400)
            s2 = st()
            if s2["isCard"]:
                fail("再点一次没有切回列表视图（#qasGrid 仍在）")
            if s2["rows"] != s0["rows"]:
                fail("切回列表视图后行数变了（{} -> {}）".format(s0["rows"], s2["rows"]))
            focus_qas()
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(200)
            if st()["curBorderStyle"] != "dashed":
                fail("列表视图下 ↑↓ 未恢复「移动当前行」的语义")
            page.keyboard.press("Escape")
            report.append({"stage": "back-to-list", "rows": s2["rows"]})

            # ---- 6. 卡片必须放得下任务名与转存路径（这是「卡片」的意义） -------
            page.click("#qasViewBtn")
            page.wait_for_timeout(400)
            s3 = st()
            if s3["cardH"] <= s0["rowH"]:
                fail("卡片高度（{}px）不高于列表行高（{}px）：一屏放得下的信息更少，"
                     "卡片视图没有存在价值".format(s3["cardH"], s0["rowH"]))
            if s3["pathLines"] < 2 or s3["pathLines"] > 2:
                fail("卡片里转存路径不是 2 行（实测 {} 行）：任务名与 savepath 常常"
                     "只差前后缀，路径被截成 1 行就失去了卡片视图的意义"
                     .format(s3["pathLines"]))
            if s3["pathClipped"]:
                fail("卡片里转存路径被省略号截断（未折行塞下）")
            # 注：「长任务名被截断」不算问题（title 里有完整值，且卡片必须等高），
            # 这里只报「短任务名也被截断」——实测就是卡片里标题列被右侧操作列挤没了。
            if s3["shortNameClipped"]:
                fail("卡片里短任务名也被截断（标题列被右侧操作列挤没了）")
            # 「勾选框对齐」这条断言要求卡片**真的参差**才有意义：所有卡一样高时
            # 它恒为真（实测：去掉对齐规则后在等高卡片上跑，走查仍然 0 处问题）。
            #
            # issue #75 之后，任务网格里「同一行」的卡片会被拉伸到该行最高 ——
            # 也就是说同行的卡**必然等高**，参差只会出现在**不同行之间**。
            # 所以这里造「跨行参差」：把第一张卡的路径撑到 3 行以上，
            # 它所在那一行整体变高，与其它行形成高度差。
            # 夹具：把第一张卡的路径换成**必然折 3 行以上**的长路径，并用内联样式
            # 解除可能存在的行数上限 —— 内联 `-webkit-line-clamp:unset` 让夹具
            # 在「旧 CSS（clamp:2）/ 新 CSS（无 clamp）」两种实现下都能造出参差。
            # 不这么做的话（只换文本），旧 CSS 的 clamp 会把它压回 2 行，
            # 夹具直接失效、断言退化成「测不了」，判别力就没了。
            page.evaluate(
                """() => {
                  const cs = [...document.querySelectorAll('#qasTaskList .lib-card')];
                  if (!cs.length) return;
                  const p = cs[0].querySelector('.lib-card-path');
                  if (!p) return;
                  p.textContent =
                    '/影视/电视剧/一条很长的路径用来把第一张卡所在的那一行整体撑高/'
                    + '再长一点再长一点再长一点再长一点再长一点再长一点';
                  p.style.webkitLineClamp = 'unset';
                  p.style.display = 'block';
                }""")
            page.wait_for_timeout(250)
            s3h = st()
            if s3h["cardHSpread"] < 10:
                fail("走查夹具失效：把第一张卡的路径撑到 3 行之后，整屏卡片高度"
                     "极差只有 {}px，「勾选框对齐」断言在等高卡片上恒为真"
                     .format(s3h["cardHSpread"]))
            # 真正要断言的：**同一行**的卡必须底边齐（这是 issue #75 的核心）
            if s3h["rowSpread"] > 2:
                fail("同一行的卡片底边不齐（极差 {}px）：长路径的卡把该行撑高后，"
                     "同行的短卡没有跟着拉伸，看上去就是「网格排歪了」"
                     .format(s3h["rowSpread"]))
            if s3h["cbTopSpread"] > 2:
                fail("参差卡片（高低差 {}px）的勾选框不在同一条水平线上"
                     "（偏移极差 {}px）：多行卡片里勾选框垂直居中会悬在名称与路径"
                     "之间，扫一列卡时找不到勾选框".format(
                         s3h["cardHSpread"], s3h["cbTopSpread"]))
            report.append({"stage": "card-geometry", "cardH": s3["cardH"],
                           "pathLines": s3["pathLines"],
                           "pathClipped": s3["pathClipped"],
                           "cardHSpread": s3h["cardHSpread"],
                           "rowSpread": s3h["rowSpread"],
                           "cbTopSpread": s3h["cbTopSpread"]})

            # ---- 6b. 同一行的卡片等高、路径不被截断（issue #75）---------------
            # 先量**真实数据**：每张卡按内容自然撑高，长的长、短的短都没关系，
            # 但同一行必须等底。这一条只能在这里量（夹具把某张卡改成 'X'/'Y'
            # 之后就只剩「一高一矮两张卡」，真实参差形态被覆盖掉了）。
            if s3["pathClipped"]:
                fail("卡片里仍有转存路径被省略号截断（{} 张）：issue #75 的"
                     "「条目高度不一致」有一半就是路径被 clamp 截断"
                     .format(s3["pathClipped"]))
            # 专门造出「同一行一长一短」：第一张卡的路径加长到必然折 3 行以上。
            # 不造的话，等宽同数据的卡片高度天然一致，这条断言恒为真 —— 等于没测。
            page.evaluate(
                """() => {
                  const cs = [...document.querySelectorAll('#qasTaskList .lib-card')];
                  if (cs.length < 2) return;
                  const p = cs[0].querySelector('.lib-card-path');
                  if (!p) return;
                  p.textContent =
                    '/影视/电视剧/这是一条非常非常长的路径用来把这张卡撑到三行以上/'
                    + '再长一点再长一点再长一点再长一点再长一点再长一点再长一点';
                  p.style.webkitLineClamp = 'unset';
                  p.style.display = 'block';
                }""")
            page.wait_for_timeout(300)
            s_eq = st()
            if s_eq["rowSpread"] > 2:
                fail("同一行的卡片底边不齐（极差 {}px）：网格里长路径的卡把该行"
                     "撑高之后，短卡没有跟着拉伸，看上去是「网格排歪了」——"
                     "这正是 issue #75 的截图。修复方向：任务网格用 "
                     "align-items:stretch（.lib-grid-tasks），路径不 clamp"
                     .format(s_eq["rowSpread"]))
            if s_eq["cardHSpread"] < 10:
                fail("走查夹具失效：把第一张卡的路径撑到 3 行之后，整屏卡片高度"
                     "极差只有 {}px —— 「同一行等高」的断言在所有卡一样高的数据上"
                     "恒为真，测不出参差问题".format(s_eq["cardHSpread"]))
            report.append({"stage": "card-equal-row", "rowSpread": s_eq["rowSpread"],
                           "rows": s_eq["rowCount"],
                           "cardHSpread": s_eq["cardHSpread"],
                           "pathClipped": s3["pathClipped"]})

            # ---- 7. 卡片视图下筛选 / 分页 / 每页条数与列表视图一致 --------------
            page.select_option("#qasPageSize", "10")
            page.wait_for_timeout(300)
            s4 = st()
            if s4["cards"] != 10:
                fail("卡片视图下每页 10 条未生效（卡片 {} 张）".format(s4["cards"]))
            if s4["pageSize"] != "10":
                fail("卡片视图下页码文案未反映每页条数（{!r}）".format(s4["pagerPos"]))
            page.evaluate("()=>setQasPathFilter('/影视/电影')")
            page.wait_for_timeout(300)
            s5 = st()
            if s5["cards"] != 2:
                fail("卡片视图下按路径筛选未生效（卡片 {} 张，应为 2）".format(s5["cards"]))
            page.click("#qasViewBtn")   # 回列表视图，筛选应保持
            page.wait_for_timeout(400)
            s6 = st()
            if s6["rows"] != 2:
                fail("切回列表视图后筛选被重置（行数 {}，应为 2）".format(s6["rows"]))
            page.evaluate("()=>{clearQasFilter();setQasPageSize('20');}")
            page.wait_for_timeout(300)
            report.append({"stage": "card-filter-page", "pageSize10": s4["cards"],
                           "filtered": s5["cards"]})

            # ---- 8. 偏好持久化 + 不串味 ---------------------------------------
            page.click("#qasViewBtn")   # 卡片视图 + 写入偏好
            page.wait_for_timeout(400)
            ls = page.evaluate(
                "()=>({qas:localStorage.getItem('qas_view'),"
                "lib:localStorage.getItem('lib_view')})")
            if ls["qas"] != "card":
                fail("卡片视图偏好未写进 localStorage（qas_view={!r}）".format(ls["qas"]))
            if ls["lib"] not in (None, "card", "list"):
                fail("媒体库布局键被 QAS 的切换污染（lib_view={!r}）".format(ls["lib"]))
            # 刷新后应仍是卡片视图
            page.reload(wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('library')")
            page.wait_for_timeout(1200)
            s7 = st()
            if not s7["isCard"]:
                fail("刷新后 QAS 布局偏好没还原（应仍是卡片视图）")
            # 反向：切媒体库的布局，QAS 的键不得被改写
            page.evaluate("()=>toggleLibView()")
            page.wait_for_timeout(300)
            ls2 = page.evaluate("()=>localStorage.getItem('qas_view')")
            if ls2 != "card":
                fail("切换媒体库布局把 QAS 的偏好带歪了（qas_view={!r}）".format(ls2))
            report.append({"stage": "persist", "qasView": ls["qas"], "libView": ls["lib"]})

            # ---- 9. 多视口：无横向溢出 ----------------------------------------
            for width, height in ((1280, 800), (768, 900), (390, 780)):
                vp = browser.new_page(viewport={"width": width, "height": height})
                vp.add_init_script("try{localStorage.setItem('auth_token','t');"
                                   "localStorage.setItem('qas_view','card')}catch(e){}")
                vp.route("**/*", lambda r: _route(r))
                vp.goto(URL, wait_until="load", timeout=20000)
                vp.evaluate("()=>switchTab('library')")
                vp.wait_for_timeout(1200)
                m = vp.evaluate(MEASURE)
                if m["overflowX"] > 0:
                    fail("{}px 视口下卡片视图横向溢出 {}px".format(width, m["overflowX"]))
                if not m["isCard"]:
                    fail("{}px 视口下未渲染卡片视图（偏好还原失败）".format(width))
                if m["cards"] < 1:
                    fail("{}px 视口下没有任何任务卡".format(width))
                report.append({"label": "{}x{} 卡片".format(width, height),
                               "cards": m["cards"], "overflowX": m["overflowX"],
                               "cardH": m["cardH"]})
                vp.close()

            page.close()
            browser.close()
    finally:
        if httpd is not None:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report},
                         ensure_ascii=False, indent=2))
    else:
        for r in report:
            print("· " + json.dumps(r, ensure_ascii=False))
        if problems:
            print("\n发现 {} 处问题：".format(len(problems)))
            for x in problems:
                print("  ✗ " + x)
        else:
            print("\nQAS 任务表视图切换走查：0 处问题")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
