#!/usr/bin/env python3
"""QAS 转存任务键盘选择的 DOM 走查（issue #53 追问：「这里也需要快捷」）。

用户诉求原文：给 QAS 任务清单的截图配了一句「这里也需要快捷」。

这类「快捷键」问题的判定**只能在渲染后做**：
  - 静态扫描（tests/test_build.py）能证明「代码里有 Ctrl+A 分支」，
    但证明不了「按下 Ctrl+A 之后真的勾上了多少行、勾选框真的被画上勾、
    操作条的「已选 N 条」真的变了、删除按钮真的不再是 disabled」；
  - 也不能只断言「函数被调用了」：「调用了但没渲染」正是这类 bug 的典型形态。

任务表比媒体库多两件事，本脚本把它们单独钉住：

  1. **作用范围 = 当前可见行**。任务表有筛选 + 分页。把媒体库那套直接搬过来
     而不收窄范围，就是「看着 20 行、按一下 Ctrl+A、实际勾上 25 条」——
     再接上「删除所选任务」，后果是删掉看不见的任务。
  2. **焦点在输入框里时必须放行**。任务表上有两个搜索框（路径包含 / 任务名包含）。
     不放行的话「打一个空格 = 勾选当前行」「Esc = 清空选择」，用户没法正常输入。

断言清单：
  1. 提示条可见（快捷键不写出来就没人知道），且与媒体库共用同一套样式口径
  2. Ctrl/⌘+A  → 只勾可见行；操作条/删除按钮/表头全选三处同步
  3. Esc       → 清空；表头全选框回到未勾、不再半选
  4. ↑↓ + 空格 → 移动当前行（虚线框可见）并勾选；空格是「切换」语义
  5. Shift+↑↓  → 从锚点连选一段，可扩可缩；不吞掉用户自己攒的选择
  6. 输入框里打字/按空格/Esc 不得被快捷键吃掉
  7. **筛选后全选不越界**：解除筛选后被筛掉的行仍是未勾选
  8. Shift+点击 / Ctrl+点击 与媒体库一致
  9. ↑↓ 到页末 → 翻到下一页并把当前行放到页首（而不是「按了没反应」）
 10. 换页后「当前行」虚线框必须重新画出来（innerHTML 重渲染会丢掉它）
 11. 换页/筛选后选择不得串到别的任务上（key 而非下标）

依赖：playwright + chromium（与 scripts/check_library_dom.py 同依赖）。
无依赖 / 无产物时以退出码 0 跳过（与其它走查脚本一致）。

用法：
  python3 scripts/check_qas_keys_dom.py            # 检查（失败非零退出）
  python3 scripts/check_qas_keys_dom.py --json     # 机器可读报告
  python3 scripts/check_qas_keys_dom.py --serve    # 自动起本地静态服务
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
PORT = 8145
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# ---------------------------------------------------------------- 桩数据
# 刻意构造的形态（与 app_modules/routes_library.py 契约一致）：
#   - 19 条 /影视/电视剧（**超过一页的 20 条上限附近**，翻页断言才走得到）；
#   - 两条 /影视/电影；
#   - 一条 /影视/TV（大写，验大小写不敏感）；
#   - 一条 /下载/临时（与「影视」无关，用来验「筛选后全选不越界」）。
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
  const boxes = rows.map(r => r.querySelector('input[type=checkbox]'));
  const cur = rows.filter(r => r.classList.contains('lib-current'));
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

            def open_page(width=1440, height=900):
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", lambda r: _route(r))
                page.goto(URL, wait_until="load", timeout=20000)
                page.evaluate("()=>switchTab('library')")
                page.wait_for_timeout(1200)
                # 键盘归属：真实用户想操作任务表时，会先点一下任务表（或它的
                # 翻页/筛选控件）。先把这个前置条件做出来 —— 否则本脚本测的是
                # 「媒体库的快捷键」，而不是任务表的（实测踩到：不加这一步，
                # Ctrl+A 全被媒体库吃掉，20 条断言集体失败）。
                page.evaluate(
                    "()=>{try{document.activeElement.blur();}catch(e){}"
                    "var c=document.getElementById('qasCard');"
                    "c.dispatchEvent(new MouseEvent('click',{bubbles:true}));}")
                page.wait_for_timeout(200)
                return page

            page = open_page()

            def st():
                return page.evaluate(MEASURE)

            def fail(msg):
                problems.append(msg)

            def press(key, times=1, wait=90):
                """连按一个键，每按一次让出一拍。

                Playwright 的 press 是异步派发的，连续调用（不等待）时间隔可能
                小于一次 re-render 的耗时，按键就会「叠」在一起 ——
                本脚本实测踩到：连按 9 次 ↓ 之后，第 10 次与第 11 次的结果
                对不上，断言报「当前行没停在末行/没翻页」，其实是脚本自己
                按得太快。生产环境的用户按键间隔 ≫ 90ms，这里照实模拟。"""
                for _ in range(times):
                    page.keyboard.press(key)
                    page.wait_for_timeout(wait)

            # 「刚点过哪张表」= 快捷键作用在哪张表（见 library.js 的 libKeyScope）。
            #
            # 前置动作：**点一下任务表的标题区**。真实用户想操作任务表时也会先点它
            # （或它的翻页/筛选控件）。不点的话，本脚本测的其实是媒体库的快捷键
            # ——实测踩到：Ctrl+A 全被媒体库吃掉，20 条断言集体失败。
            #
            # 用 dispatchEvent 而不是 page.click：#qasKeyHint 在窄视口是
            # display:none（设计如此），click 在那里会超时 30s；而归属判定
            # 只关心「事件来自哪张卡」，事件合成即可，不需要元素可点。
            def focus_qas():
                # 两件事一起做，对应真实用户的一个动作（点一下任务表）：
                #   ① 把键盘归属收到任务表（libKeyScope）；
                #   ② 把焦点从搜索框里挪开 —— 用户敲完路径搜索后会点列表，
                #      而不是继续在输入框里按方向键。不挪开的话，_qasKeydown
                #      会（正确地）放行给输入框，走查就全都在测输入框。
                page.evaluate(
                    "()=>{try{document.activeElement.blur();}catch(e){}"
                    "var c=document.getElementById('qasCard');"
                    "c.dispatchEvent(new MouseEvent('click',{bubbles:true}));}")
                page.wait_for_timeout(150)

            def reset(page_size=None):
                """回到「无筛选、无选择、每页 20（或指定值）」的干净起点。

                `page_size` 一定要在这里一起设：翻页断言依赖「本页 10 行」，
                而这个每页条数会被**持久化到 localStorage**（见 setQasPageSize），
                跨步骤残留。曾经把它放在步骤里单独 select_option，
                结果上一步的 pageSize 与这一步的行数对不上，
                断言报出来的是「翻页错位」——其实是脚本自己状态没清干净。
                """
                if page_size is not None:
                    page.select_option("#qasPageSize", str(page_size))
                    page.wait_for_timeout(200)
                page.evaluate("()=>clearQasFilter()")
                page.evaluate("()=>toggleQasSelectAll(false)")
                page.evaluate("()=>toggleQasSelectAll(false)")
                # 光标与范围基线也一起清：否则「上一步停在某一行」会让
                # 「连按 N 次 ↓ → 应该停在末行」的算术全部错位。
                page.evaluate("()=>{qasFocusKey=null;qasSelectAnchor=null;qasRangeBase=null;}")
                focus_qas()
                page.wait_for_timeout(200)

            s0 = st()
            if s0["rows"] != min(20, TOTAL):
                fail("未筛选时首屏应渲染 {} 行，实际 {} 行（后面断言不可信）".format(
                    min(20, TOTAL), s0["rows"]))
                report.append({"stage": "initial", "rows": s0["rows"]})
                browser.close()
                return 1

            # 0. 提示条：快捷键不写出来就没人知道（诉求本身就是「不知道怎么选」）
            if s0["hintDisplay"] == "none":
                fail("任务表没有可见的键盘操作提示（#qasKeyHint）—— "
                     "0.3 秒能看懂的提示条，比文档里写三行有用")
            # 与媒体库共用同一套样式口径：字号必须一致（否则两张表像两个人做的）
            if s0["hintFontSize"] != s0["hintLibFontSize"] or not s0["hintFontSize"]:
                fail("任务表提示条字号 {} 与媒体库 {} 不一致"
                     "（两处口径漂移 = 在 900px 断点上会一个藏一个不藏）".format(
                         s0["hintFontSize"], s0["hintLibFontSize"]))
            report.append({"stage": "hint", "display": s0["hintDisplay"],
                           "font": s0["hintFontSize"]})

            n = s0["rows"]

            # 1. Ctrl/⌘+A：只勾可见行，且三处状态同步
            page.keyboard.press("Control+a")
            page.wait_for_timeout(200)
            s = st()
            if s["checked"] != n:
                fail("Ctrl+A 后勾选框只有 {} / {} 个为选中（内部状态与屏幕不一致）".format(
                    s["checked"], n))
            if "已选 {} 条".format(n) not in s["selCount"]:
                fail("Ctrl+A 后操作条计数未反映全选（{!r}）".format(s["selCount"]))
            if s["delDisabled"] is not False:
                fail("Ctrl+A 后「删除所选任务」仍是禁用态")
            if s["allChecked"] is not True or s["allIndeterminate"]:
                fail("Ctrl+A 后表头全选框状态不对（checked={} indeterminate={}）".format(
                    s["allChecked"], s["allIndeterminate"]))
            report.append({"stage": "ctrl+a", "rows": n, "checked": s["checked"],
                           "selCount": s["selCount"]})

            # 2. Esc 清空
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            s = st()
            if s["checked"] != 0 or s["selCount"] != "":
                fail("Esc 未清空选择（checked={} selCount={!r}）".format(
                    s["checked"], s["selCount"]))
            if s["allChecked"] or s["allIndeterminate"]:
                fail("Esc 后表头全选框仍有状态")
            if s["delDisabled"] is not True:
                fail("清空后「删除所选任务」未回到禁用态")

            # 3. ↑↓ 移动当前行（虚线可见）+ 空格切换
            press("ArrowDown", 2, wait=150)
            page.wait_for_timeout(200)
            s = st()
            if s["curCount"] != 1:
                fail("方向键未标出唯一的「当前行」（{} 个）".format(s["curCount"]))
            if s["curBorderStyle"] != "dashed":
                fail("「当前行」没有虚线框标记（border-style={!r}）——"
                     "用户看不出 ↑↓ 会从哪一行开始".format(s["curBorderStyle"]))
            if s["checked"] != 0:
                fail("纯 ↑↓ 不应改变选择（checked={}）".format(s["checked"]))
            top = s["curKey"]
            page.keyboard.press(" ")
            page.wait_for_timeout(150)
            s = st()
            if s["checked"] != 1 or s["checkedKeys"] != [top]:
                fail("空格未勾选当前行（checked={} keys={}）".format(
                    s["checked"], s["checkedKeys"]))
            page.keyboard.press(" ")
            page.wait_for_timeout(150)
            if st()["checked"] != 0:
                fail("空格第二次按下未取消勾选（不是「切换」语义）")
            report.append({"stage": "space-toggle", "key": top})

            # 4. Shift+↓ 连选 → Shift+↑ 缩回（不是只增不减）
            reset()
            press("ArrowDown", 3, wait=150)
            page.wait_for_timeout(180)
            press("Shift+ArrowDown", 2, wait=150)
            page.wait_for_timeout(200)
            s = st()
            if s["checked"] != 3:
                fail("Shift+↓ 连选应为 3 行（第 3~5 行），实际 {}（keys={}）".format(
                    s["checked"], s["checkedKeys"]))
            if s["curCount"] != 1:
                fail("Shift 连选后应只有唯一的「当前行」（{} 个）".format(s["curCount"]))
            if s["checkedKeys"] and s["curKey"] != s["checkedKeys"][-1]:
                fail("Shift 连选后「当前行」未停在选区末端（cur={} keys={}）".format(
                    s["curKey"], s["checkedKeys"]))
            press("Shift+ArrowUp", 1, wait=200)
            if st()["checked"] != 2:
                fail("Shift+↑ 未把选区缩回 2 行（checked={}）—— 只能扩不能缩等于没有范围选择".format(
                    st()["checked"]))
            report.append({"stage": "shift-range", "keys": st()["checkedKeys"]})

            # 4b. Shift 扩选不得吞掉用户自己攒的选择（文件管理器语义）
            reset()
            rows_loc = "#qasTaskList .lib-row:not(.lib-head)"
            page.locator(rows_loc).nth(1).click()
            page.wait_for_timeout(120)
            page.locator(rows_loc).nth(6).click(modifiers=["Control"])
            page.wait_for_timeout(150)
            kept = st()["checkedKeys"]
            page.locator(rows_loc).nth(8).click(modifiers=["Shift"])
            page.wait_for_timeout(200)
            s = st()
            if s["checked"] != 4:
                fail("Shift 扩选吞掉了先前 Ctrl+点击的选择（checked={} keys={}，"
                     "期望 4 = 范围 3 行 + 范围外 1 行）".format(s["checked"], s["checkedKeys"]))
            lost = [k for k in kept if k not in s["checkedKeys"]]
            if lost:
                fail("Shift 扩选后丢失了先前选中的行 {}".format(lost))
            report.append({"stage": "shift-keeps-others", "keys": s["checkedKeys"]})

            # 5. 焦点在输入框里时一律放行（打空格 = 打字，不是勾选）
            reset()
            page.click("#qasFilterPath")
            page.fill("#qasFilterPath", "/影视/电视剧")
            page.wait_for_timeout(250)
            s = st()
            if s["checked"] != 0:
                fail("在搜索框里打字竟勾上了 {} 行（快捷键没给输入框让路）".format(s["checked"]))
            page.keyboard.press("Space")
            page.wait_for_timeout(200)
            s = st()
            if s["checked"] != 0:
                fail("在搜索框里按空格被当成「勾选当前行」（用户没法输入带空格的关键词）")
            if not s["pathValue"].endswith(" "):
                fail("在搜索框里按空格没有真的输入空格（value={!r}）".format(s["pathValue"]))
            report.append({"stage": "input-passthrough", "value": s["pathValue"]})
            # 从输入框里出来（真实用户会点列表继续操作）：不 blur 的话，
            # 后面所有按键都会被（正确地）放行给输入框，走查测的就全是输入框。
            page.evaluate("()=>{try{document.activeElement.blur();}catch(e){}}")
            page.wait_for_timeout(120)

            # 6. 筛选后 Ctrl+A：只勾可见行，解除筛选后被筛掉的行必须仍未勾选
            #    （Task 表上「看着 19 行、勾上 24 条」是真实可达的失败模式）
            reset()
            page.evaluate("()=>{qasFocusKey=null;qasSelectAnchor=null;qasRangeBase=null;}")
            page.fill("#qasFilterPath", "/影视/电视剧")
            page.evaluate("()=>{try{document.activeElement.blur();}catch(e){}}")
            page.wait_for_timeout(300)
            vis = st()["rows"]
            if vis != min(EXPECT_TV, 20):
                fail("筛选 /影视/电视剧 后应渲染 {} 行，实际 {}".format(
                    min(EXPECT_TV, 20), vis))
            page.keyboard.press("Control+a")
            page.wait_for_timeout(250)
            s = st()
            if s["checked"] != vis:
                fail("筛选后 Ctrl+A 应只勾可见的 {} 行，实际 {}".format(vis, s["checked"]))
            page.evaluate("()=>clearQasFilter()")
            page.wait_for_timeout(300)
            s = st()
            if any(r for i, r in enumerate(s["checkedKeys"])
                   if not s["paths"][s["keys"].index(r)].startswith("/影视/电视剧")):
                fail("解除筛选后被筛掉的行仍是勾选态 —— 全选越界了")
            if s["selCount"].split("（")[0] != "已选 {} 条".format(vis):
                fail("解除筛选后操作条计数不对（{!r}，期望已选 {} 条）".format(
                    s["selCount"], vis))
            if s["allIndeterminate"] is not True:
                fail("部分选中时表头全选框应为半选态（indeterminate）")
            report.append({"stage": "select-visible-only", "selCount": s["selCount"]})

            # 7. 翻页：↑↓ 到页末要能接着翻页（不是「按了没反应」），
            #    且换页后「当前行」虚线框必须重新画出来
            reset()
            page.select_option("#qasPageSize", "10")
            page.wait_for_timeout(350)
            # 每页条数变了 = 行重排 = 旧光标/旧基线失效（真实用户会重新点第一行）
            page.evaluate("()=>{qasFocusKey=null;qasSelectAnchor=null;qasRangeBase=null;}")
            focus_qas()
            page.wait_for_timeout(250)
            s = st()
            if "第 1 / " not in s["pagerPos"]:
                fail("每页 10 条时不在第 1 页（{!r}）".format(s["pagerPos"]))
            # 光标压到本页最后一行
            press("ArrowDown", 9)
            page.wait_for_timeout(200)
            s = st()
            last_key = s["keys"][-1]
            if s["curKey"] != last_key:
                fail("连按 ↓ 后当前行未停在本页末行（cur={} 末行={}）".format(
                    s["curKey"], last_key))
            first_path = s["paths"][0]
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(350)
            s = st()
            if "第 2 / " not in s["pagerPos"]:
                fail("在页末再按 ↓ 没有翻页（{!r}）—— 快捷键的价值被分页吃掉一半".format(
                    s["pagerPos"]))
            if s["paths"] and s["paths"][0] == first_path:
                fail("翻页后列表内容没变（第 2 页与第 1 页首行相同）")
            if s["curKey"] != s["keys"][0]:
                fail("翻页后「当前行」未落在本页首行（cur={} 首行={}）".format(
                    s["curKey"], s["keys"][0]))
            if s["curCount"] != 1 or s["curBorderStyle"] != "dashed":
                fail("翻页后「当前行」虚线框丢了（count={} border={!r}）—— "
                     "innerHTML 重渲染会清掉标记，必须补回来".format(
                         s["curCount"], s["curBorderStyle"]))
            # 在页首再按 ↑ 回到上一页
            # 回到第 1 页之前先把光标放到本页首行（上一段末尾它停在末行）
            page.evaluate("()=>qasFocusRow(0)")
            page.wait_for_timeout(150)
            press("ArrowUp", 1, wait=400)
            s = st()
            if "第 1 / " not in s["pagerPos"]:
                fail("在第 2 页首行按 ↑ 未回到第 1 页（{!r}）".format(s["pagerPos"]))
            if s["curKey"] != s["keys"][-1]:
                fail("回到上一页后「当前行」未落在该页末行（cur={} 末行={}）".format(
                    s["curKey"], s["keys"][-1]))
            report.append({"stage": "page-turn", "pos": s["pagerPos"]})
            page.select_option("#qasPageSize", "20")
            page.wait_for_timeout(300)

            # 8. 换页不得让选择串到别的任务上（key 而非下标）
            reset()
            page.locator(rows_loc).nth(0).click()
            page.wait_for_timeout(150)
            picked = st()["checkedKeys"]
            page.evaluate("()=>qasPage(1)")
            page.wait_for_timeout(250)
            page.evaluate("()=>qasPage(-1)")
            page.wait_for_timeout(250)
            s = st()
            if s["checkedKeys"] != picked:
                fail("换页往返后勾选的 key 变了（{} -> {}）—— 选择按「下标」而非 "
                     "「任务 key」保存，会串到别的任务上".format(picked, s["checkedKeys"]))
            report.append({"stage": "selection-by-key", "keys": s["checkedKeys"]})

            # 9. 窄视口：提示条收起（触屏没有这些键），且不得把页面撑出横向滚动条
            for width in (390, 768):
                sp = open_page(width=width, height=844)
                sp.wait_for_timeout(300)
                ss = sp.evaluate(MEASURE)
                if ss["hintDisplay"] != "none":
                    fail("{}px 视口任务表提示条未收起（display={}）".format(
                        width, ss["hintDisplay"]))
                if ss["overflowX"] > 1:
                    fail("{}px 视口出现横向溢出（{}px）".format(width, ss["overflowX"]))
                report.append({"stage": "viewport-{}".format(width),
                               "hint": ss["hintDisplay"], "overflowX": ss["overflowX"]})
                sp.close()

            # 10. 窄视口：行不加 tabindex（提示条已收起，不该再让 Tab 键空走几十次），
            #     但**快捷键照旧可用**。
            #
            # 为什么快捷键不跟着视口关掉：窄视口 ≠ 没有键盘（缩窄的窗口、
            # 带键盘的平板都很常见），把「看不见提示条」和「用不了快捷键」绑在
            # 一起，受伤的正是最需要快捷键的人。既有的 check_library_density_dom.py
            # 在 390px 视口下明确要求 Ctrl+A / Shift+↓ / 当前行照旧生效 ——
            # 那里就是这个口径，两处必须一致。
            sp = open_page(width=390, height=844)
            sp.wait_for_timeout(300)
            before = sp.evaluate(MEASURE)
            sp.keyboard.press("Control+a")
            sp.wait_for_timeout(250)
            after = sp.evaluate(MEASURE)
            if after["checked"] <= before["checked"]:
                fail("390px 视口下 Ctrl+A 未生效（checked {} -> {}）—— "
                     "窄视口不等于没有键盘，快捷键不应随视口关闭".format(
                         before["checked"], after["checked"]))
            report.append({"stage": "narrow-cursor", "rows": after["rows"],
                           "checked": after["checked"],
                           "tabindex": sp.evaluate(
                               "()=>{var r=document.querySelector('#qasTaskList .lib-row');"
                               "return r ? r.getAttribute('tabindex') : null}")})
            sp.close()

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
            print("\nQAS 任务键盘选择走查：0 处问题")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
