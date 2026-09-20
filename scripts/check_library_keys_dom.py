#!/usr/bin/env python3
"""媒体库键盘选择的 DOM 走查（issue #53）。

用户诉求原文：「我希望在使用电脑进行选中时，可以使用电脑的快捷键进行选择。
当前的选中效率太低了。没有批量全选和小部分范围的选择快捷键」。

这类「快捷键」问题的判定**只能在渲染后做**：
  - 静态扫描（tests/test_build.py）能证明「代码里有 Ctrl+A 分支」，
    但证明不了「按下 Ctrl+A 之后真的选中了 24 项、勾选框真的被画上勾、
    操作条的「已选 N 项」真的变了、删除按钮真的不再是 disabled」；
  - 也不能只断言「函数被调用了」：「调用了但没渲染」正是这类 bug 的典型形态。

所以本脚本真的按键盘、真的读 DOM，逐条断言：

  1. Ctrl/⌘+A          → 全选，逐行勾选框 DOM 属性同步（不是只改了内部对象）
  2. Esc                → 清空，且表头全选回到未勾、不再半选
  3. Shift+方向键        → 从锚点连选一段（「小部分范围选择」），可扩可缩
  4. 方向键 + 空格        → 移动当前行并勾选（真切换：第二次按下必须取消）；
                           当前行有可见的「虚线框」标记
  5. Ctrl/⌘+点击         → 逐个增减（累加式多选）
  6. Shift+点击          → 从锚点连选到该行
  7. 卡片 / 列表两种视图  → 同一套语义，**方向键按视觉方向各取所属的一对**：
                           列表视图（表格，顺序是上下）= ↑↓；
                           卡片视图（网格，顺序是左右）= ←→，
                           此时 ↑↓ 让位给浏览器滚动。
                           这与任务表卡片视图的口径逐字一致（见 _qasKeydown）。
  8. 越界              → 光标停在边界不回绕（否则 Shift 连选会瞬间全选）

依赖：playwright + chromium（与 scripts/check_library_dom.py 同依赖）。
无依赖 / 无产物时以退出码 0 跳过（与其它走查脚本一致）。

用法：
  python3 scripts/check_library_keys_dom.py            # 检查（失败非零退出）
  python3 scripts/check_library_keys_dom.py --json     # 机器可读报告
  python3 scripts/check_library_keys_dom.py --serve    # 自动起本地静态服务
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
PORT = 8136
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# 目录条目：12 条（目录 + 文件混排），足够覆盖「范围连选要跨多行」。
ENTRIES = [
    {"fid": "d%d" % i, "name": "目录-%02d" % i, "dir": True, "size": 0,
     "updated_at": 1758000000 + i}
    for i in range(1, 4)
] + [
    {"fid": "f%d" % i, "name": "文件-%02d.mkv" % i, "dir": False,
     "size": 1024 ** 3 * i, "updated_at": 1758000100 + i}
    for i in range(1, 10)
]

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
        body = {"success": True, "path": "/", "entries": ENTRIES,
                "source": "openlist", "fallback": False}
    else:
        body = STUBS.get(path, {})
    return route.fulfill(status=200, content_type="application/json",
                         body=json.dumps(body, ensure_ascii=False).encode())


# 断言用的度量：全部基于「渲染后的勾选框 DOM 属性 + 操作条文案 + 选中行样式」，
# 而不是内部的 libSelected 对象 —— 后者只能证明「代码以为自己选了」。
MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  // 两种视图的行：卡片视图在 #libGrid，列表视图在 #libList .lib-table
  // （列表视图**没有** #libGrid，只查 #libGrid 会得到 0 行 —— 本轮实测踩到）。
  const rows = [...document.querySelectorAll(
    '#libGrid .lib-card, #libGrid .lib-row:not(.lib-head),'
    + ' #libList .lib-table .lib-row:not(.lib-head)')];
  const boxes = rows.map(r => r.querySelector('input[type=checkbox]'));
  const cur = rows.filter(r => r.classList.contains('lib-current'));
  const checkedBg = boxes.filter(b => b.checked).map(b => {
    const row = b.closest('.lib-card, .lib-row');
    return getComputedStyle(row).backgroundColor;
  });
  const bar = q('#libActionBar');
  const all = q('#libSelAll');
  return {
    rows: rows.length,
    checked: boxes.filter(b => b.checked).length,
    checkedKeys: rows.filter(r => {
      const b = r.querySelector('input[type=checkbox]');
      return b && b.checked;
    }).map(r => r.getAttribute('data-key')),
    // 「当前行」：虚线标记 + class 双重证据（只看 class 会漏掉「标记了但样式丢了」）。
    // 统一取 border-*top*-style：卡片视图用整圈虚线，表格行只加一道虚线顶边
    // （底边要保留原本的行分隔线），两者取 top 都能拿到 dashed。
    curCount: cur.length,
    curKey: cur.length ? cur[0].getAttribute('data-key') : null,
    curBorderStyle: cur.length
      ? getComputedStyle(cur[0]).borderTopStyle : '',
    // 选中行底色：全选后必须能一眼看出哪些行被选中（不能只有勾选框变蓝）
    checkedBg: checkedBg.slice(0, 3),
    countText: q('#libSelCount') ? q('#libSelCount').textContent : '',
    delDisabled: q('#libDelBtn') ? q('#libDelBtn').disabled : null,
    barDisplay: bar ? getComputedStyle(bar).display : '',
    allChecked: all ? all.checked : null,
    allIndeterminate: all ? all.indeterminate : null,
    keyHintVisible: (function(){
      const h = q('#libKeyHint');
      return !!h && getComputedStyle(h).display !== 'none';
    })(),
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

            for view, label in (("card", "卡片视图"), ("list", "列表视图")):
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", lambda r: _route(r))
                page.goto(URL, wait_until="load", timeout=20000)
                page.evaluate("()=>switchTab('library')")
                page.wait_for_timeout(1200)
                if view == "list":
                    page.evaluate("()=>toggleLibView()")
                    page.wait_for_timeout(400)

                def st():
                    return page.evaluate(MEASURE)

                def fail(msg):
                    problems.append("[{}] {}".format(label, msg))

                # 方向键的「前一张 / 后一张」按视觉方向取：
                # 列表视图是表格（上下顺序）→ ↑↓；卡片视图是网格（左右顺序）→ ←→。
                # 卡片视图默认也是这个脚本的主场景，所以必须由 view 决定，
                # 不能写死一个 —— 旧版本写死 ↑↓，在卡片视图下它会去滚页面，
                # 于是「Shift 连选」的断言在那条路径上从来没被真正执行过。
                PREV, NEXT = ("ArrowLeft", "ArrowRight") if view == "card" else ("ArrowUp", "ArrowDown")

                s0 = st()
                if s0["rows"] < 8:
                    fail("目录行数异常（{} 行），后面的断言不可信".format(s0["rows"]))
                    page.close()
                    continue
                n = s0["rows"]

                # 0. 提示条：快捷键不写出来就没人知道（issue #53 的诉求本身）
                if not s0["keyHintVisible"]:
                    fail("目录栏没有可见的键盘操作提示（#libKeyHint）")

                # 1. Ctrl/⌘+A 全选：必须逐行同步勾选框 DOM，不只是内部状态
                page.keyboard.press("Control+a")
                page.wait_for_timeout(150)
                s = st()
                if s["checked"] != n:
                    fail("Ctrl+A 后勾选框只有 {} / {} 个为选中".format(s["checked"], n))
                if "已选 {} 项".format(n) not in s["countText"]:
                    fail("Ctrl+A 后操作条计数未反映全选（{!r}）".format(s["countText"]))
                if s["delDisabled"] is not False:
                    fail("Ctrl+A 后「删除所选」仍是禁用态")
                if s["allChecked"] is not True or s["allIndeterminate"]:
                    fail("Ctrl+A 后表头全选框状态不对（checked={} indeterminate={}）".format(
                        s["allChecked"], s["allIndeterminate"]))
                if not s["checkedBg"] or all(c == "rgba(0, 0, 0, 0)" for c in s["checkedBg"]):
                    fail("全选后选中行没有底色标记（{}），只能靠 18px 勾选框辨认".format(
                        s["checkedBg"]))
                report.append({"label": label + " Ctrl+A", "rows": n,
                               "checked": s["checked"], "count": s["countText"]})

                # 2. Esc 清空
                page.keyboard.press("Escape")
                page.wait_for_timeout(150)
                s = st()
                if s["checked"] != 0 or s["countText"] != "":
                    fail("Esc 未清空选择（checked={} count={!r}）".format(
                        s["checked"], s["countText"]))
                if s["allChecked"] or s["allIndeterminate"]:
                    fail("Esc 后表头全选框仍有状态（checked={} indeterminate={}）".format(
                        s["allChecked"], s["allIndeterminate"]))
                if s["delDisabled"] is not True:
                    fail("清空后「删除所选」未回到禁用态")

                # 3. 方向键移动当前行 + Space 勾选（「当前行」必须有可见标记）
                page.keyboard.press(NEXT)
                page.keyboard.press(NEXT)
                page.wait_for_timeout(150)
                s = st()
                if s["curCount"] != 1:
                    fail("方向键未标出唯一的「当前行」（{} 个）".format(s["curCount"]))
                if s["curBorderStyle"] != "dashed":
                    fail("「当前行」没有虚线框标记（border-style={!r}）—— "
                         "用户看不出方向键会从哪一行开始".format(s["curBorderStyle"]))
                if s["checked"] != 0:
                    fail("纯移动方向键不应改变选择（checked={}）".format(s["checked"]))
                top_key = s["curKey"]
                page.keyboard.press(" ")
                page.wait_for_timeout(120)
                s = st()
                if s["checked"] != 1 or s["checkedKeys"] != [top_key]:
                    fail("空格未勾选当前行（checked={} keys={}）".format(
                        s["checked"], s["checkedKeys"]))
                # 再按一次空格应取消（真切换）
                page.keyboard.press(" ")
                page.wait_for_timeout(120)
                if st()["checked"] != 0:
                    fail("空格第二次按下未取消勾选（不是「切换」语义）")

                # 4. Shift+↓ 从锚点连选一段（issue 原文的「小部分范围选择」）
                page.evaluate("()=>libClearSelection()")
                page.wait_for_timeout(80)
                page.keyboard.press(NEXT)        # 第 1 行
                page.keyboard.press(NEXT)        # 第 2 行：锚点在此
                page.keyboard.press(NEXT)        # 第 3 行：焦点在此
                page.wait_for_timeout(120)
                page.keyboard.press("Shift+" + NEXT)
                page.keyboard.press("Shift+" + NEXT)
                page.wait_for_timeout(150)
                s = st()
                # 第 3 行按了两次 Shift+方向键 → 覆盖第 3、4、5 行共 3 行
                if s["checked"] != 3:
                    fail("Shift+方向键连选应为 3 行，实际 {} 行（keys={}）".format(
                        s["checked"], s["checkedKeys"]))
                if s["curCount"] != 1:
                    fail("Shift 连选后应只有唯一的「当前行」（{} 个）".format(s["curCount"]))
                if s["checkedKeys"] and s["curKey"] != s["checkedKeys"][-1]:
                    fail("Shift 连选后「当前行」未停在选区末端（cur={} keys={}）".format(
                        s["curKey"], s["checkedKeys"]))
                report.append({"label": label + " Shift+↓", "keys": s["checkedKeys"],
                               "count": s["countText"]})

                # 4b. Shift+↑ 应缩回（不是只增不减）
                page.keyboard.press("Shift+" + PREV)
                page.wait_for_timeout(150)
                if st()["checked"] != 2:
                    fail("Shift+反向键未把选区缩回 2 行（checked={}）".format(st()["checked"]))

                # 4c. 越界不回绕：已经在第一行时再按反向键，选区不得跳到末尾
                page.evaluate("()=>libClearSelection()")
                page.wait_for_timeout(80)
                # libClearSelection() 会把「当前行」标记也清掉（这是对的，见它的
                # 注释），所以先用一次正向键把光标建起来，再一路按反向键撞边界
                # —— 否则一上来就按反向键，光标从「没有」直接变成第 1 行，
                # 撞不到边界，「停在第一行」这条断言等于没测（本轮实测踩到）。
                page.keyboard.press(NEXT)
                page.wait_for_timeout(80)
                for _ in range(n + 3):
                    page.keyboard.press(PREV)
                page.wait_for_timeout(100)
                s = st()
                first_key = page.evaluate(
                    "()=>{var r=document.querySelector("
                    "'#libGrid .lib-card, #libGrid .lib-row:not(.lib-head),"
                    " #libList .lib-table .lib-row:not(.lib-head)');"
                    "return r ? r.getAttribute('data-key') : null}")
                if s["curKey"] != first_key:
                    fail("到边界后回绕了（cur={}，应停在第一行 {}）".format(
                        s["curKey"], first_key))
                page.keyboard.press("Shift+" + PREV)
                page.wait_for_timeout(100)
                if st()["checked"] > 1:
                    fail("在首行按 Shift+反向键竟选出了多行（回绕导致）")

                # 5. Ctrl/⌘+点击：逐个增减（累加式多选）
                page.evaluate("()=>libClearSelection()")
                page.wait_for_timeout(80)
                rows_loc = ("#libGrid .lib-card" if view == "card"
                            else "#libList .lib-table .lib-row:not(.lib-head)")
                page.locator(rows_loc).nth(0).click()
                page.wait_for_timeout(120)
                page.locator(rows_loc).nth(1).click(modifiers=["Control"])
                page.wait_for_timeout(150)
                s = st()
                if s["checked"] != 2:
                    fail("Ctrl+点击未实现「逐个增减」（checked={}，期望 2）".format(s["checked"]))
                # 再 Ctrl+点同一行 → 取消该行
                page.locator(rows_loc).nth(1).click(modifiers=["Control"])
                page.wait_for_timeout(150)
                s = st()
                if s["checked"] != 1:
                    fail("Ctrl+点击已选行未取消（checked={}，期望 1）".format(s["checked"]))

                # 6. Shift+点击：从锚点连选到该行
                page.evaluate("()=>libClearSelection()")
                page.wait_for_timeout(80)
                page.locator(rows_loc).nth(1).click()
                page.wait_for_timeout(120)
                page.locator(rows_loc).nth(4).click(modifiers=["Shift"])
                page.wait_for_timeout(150)
                s = st()
                if s["checked"] != 4:
                    fail("Shift+点击未从锚点连选（checked={}，期望 4：第 2~5 行）".format(
                        s["checked"]))

                # 7. Ctrl/⌘+方向键：移动当前行但不改变选择。
                #    卡片视图里 ↑↓ 让位给浏览器滚动（与任务表同口径），
                #    所以这一条只在列表视图断言；卡片视图改断言「让位」本身。
                before = st()["checked"]
                if view == "list":
                    page.keyboard.press("Control+" + NEXT)
                    page.wait_for_timeout(120)
                    if st()["checked"] != before:
                        fail("Ctrl+方向键不应改变选择（{} -> {}）".format(before, st()["checked"]))
                else:
                    cur_before = st()["curKey"]
                    page.keyboard.press("ArrowDown")
                    page.wait_for_timeout(120)
                    if st()["curKey"] != cur_before:
                        fail("卡片视图下 ↑↓ 不应移动「当前行」（那是浏览器滚动的位置）")
                    if st()["checked"] != before:
                        fail("卡片视图下 ↑↓ 不应改变选择（{} -> {}）".format(
                            before, st()["checked"]))

                # 8. Shift 扩选不得吞掉用户自己攒的选择（文件管理器语义）。
                #    这是最容易被写成「纯覆盖」的一条：先 Ctrl 点 2 个、再 Shift 扩一段，
                #    结果只剩范围里那几个 —— 用户会说「我点的东西自己没了」。
                page.evaluate("()=>libClearSelection()")
                page.wait_for_timeout(80)
                # 锚点/焦点语义与桌面端一致：**Ctrl+点击也会移动锚点**，
                # 所以「Ctrl 点第 7 行」之后 Shift 的范围从第 7 行起算。
                page.locator(rows_loc).nth(1).click()
                page.wait_for_timeout(100)
                page.locator(rows_loc).nth(6).click(modifiers=["Control"])
                page.wait_for_timeout(120)
                kept = st()["checkedKeys"]
                # Shift 扩到第 9 行：范围 = 第 7~9 行共 3 行，
                # 第 2 行（nth(1)，范围之外、用户自己点的）必须保留 → 4 项
                page.locator(rows_loc).nth(8).click(modifiers=["Shift"])
                page.wait_for_timeout(150)
                s = st()
                if s["checked"] != 4:
                    fail("Shift 扩选吞掉了先前点击的选择（checked={} keys={}，"
                         "期望 4 = 范围 3 行 + 范围外的 1 行）".format(
                             s["checked"], s["checkedKeys"]))
                lost = [k for k in kept if k not in s["checkedKeys"]]
                if lost:
                    fail("Shift 扩选后丢失了先前选中的行 {}".format(lost))

                # 9. 换目录后选择与「当前行」都必须清空（不能把上一个目录的选择带过去）
                page.evaluate("()=>enterDir('目录-01')")
                page.wait_for_timeout(800)
                s = st()
                if s["checked"] != 0 or s["curCount"] != 0:
                    fail("换目录后仍有残留选择/当前行（checked={} cur={}）".format(
                        s["checked"], s["curCount"]))
                page.evaluate("()=>loadLibrary('/')")
                page.wait_for_timeout(800)

                report.append({"label": label + " 收尾", **{k: st()[k] for k in (
                    "checked", "countText", "allIndeterminate")}})
                page.close()

            browser.close()
    finally:
        if httpd is not None:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report},
                         ensure_ascii=False, indent=2))
    else:
        for m in report:
            print("{:<20} {}".format(m.pop("label"), json.dumps(
                m, ensure_ascii=False)))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for x in problems:
                print("  - " + x)
            return 1
        print("媒体库键盘选择走查通过（0 处问题）")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
