#!/usr/bin/env python3
"""媒体库「选中 / 取消选中」走查（issue #68）。

用户原话：「在选中后无法通过再点击选中框，来取消选中」。
配图里 6 个框全勾着、鼠标正指着第一个勾选框 —— 再点它没有任何反应。

这是 media library 的一个**开关失效**：勾选框只有「开」，没有「关」。
成因是 libCardClick 里一句 `var on = multi ? !libSelected[key] : true;` ——
普通点击被写死为「选中」，取消只在 Ctrl/⌘+点击 这条路上存在。

静态扫描（tests/test_build.py）证明不了「点下去真的翻了」，所以这里用真实浏览器，
对**用户能做的每一种取消方式**逐一取数：

  1. 点勾选框：勾上 → 再点 → 必须取消（issue #68 的直接复现路径）；
  2. 点行的空白处：同上（卡片视图里那是 <label> 外的区域，列表视图是整行）；
  3. Ctrl/⌘+点击：逐个增减（这条在改动前就是好的，作为回归护栏留着）；
  4. 空格键（焦点在行上）：切换；
  5. Ctrl/⌘+A 全选 → Esc 清空（顺带确认全选仍是「只勾可见行」）。

每种方式都断言**两处一致性**，缺一不可：
  * `libSelected`（逻辑状态）—— 决定「删除所选」到底删什么；
  * `#libList input:checked` 的条数（屏幕状态）—— 用户眼睛看到的。
两者不一致就是「看着选了、删的时候没有」或反过来，是最贵的一类 bug。

依赖/用法与其它走查脚本一致：
  python3 scripts/check_library_selection_dom.py --serve
无 playwright 时以退出码 0 跳过（不影响不装浏览器的 CI 阶段）。
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
PORT = 8131
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# 条目数量固定为 6：足够验证「取消一条后其余不受影响」，又不至于让断言难读。
_LIB_ENTRIES = [
    {"fid": "f{}".format(i), "name": "作品{}.mkv".format(i), "dir": False,
     "size": (i + 1) * 1024 ** 3, "updated_at": 1758000000 + i}
    for i in range(6)
]
_QAS_TASKS = {"success": True, "tasks": [
    {"taskname": "示例任务-{:02d}".format(i), "savepath": "/媒体库/电影",
     "shareurl": "https://pan.quark.cn/s/demo{:02d}".format(i)}
    for i in range(3)
]}

# 读取「逻辑状态 + 屏幕状态 + 操作条计数」的统一探针。
# 三个口径必须同时看：只看 libSelected 会漏掉「勾选框没同步」，
# 只看勾选框会漏掉「删的时候用的是另一份状态」。
PROBE = r"""() => ({
  sel: Object.keys(libSelected).length,
  keys: Object.keys(libSelected).sort(),
  checked: document.querySelectorAll('#libList input[type=checkbox]:checked').length,
  count: (document.getElementById('libSelCount') || {}).textContent || '',
  delDisabled: (document.getElementById('libDelBtn') || {}).disabled,
  rows: document.querySelectorAll('#libList .lib-card, #libList .lib-row:not(.lib-head)').length,
})"""


def _launch_kwargs():
    """本机 playwright 版本与已装 chromium 目录不匹配时回退到显式可执行文件。"""
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


def _route(route, log):
    url = route.request.url
    if "/api/" not in url:
        return route.fallback()
    path = url.split(str(PORT), 1)[-1].split("?", 1)[0]
    if path.rstrip("/").endswith("/api/sse"):
        return route.fulfill(status=200, content_type="text/event-stream", body=b"")
    if path == "/api/qas/library":
        qp = urllib.parse.unquote((url.split("path=", 1)[1] if "path=" in url else "/").split("&")[0])
        body = {"success": True, "path": qp, "entries": list(_LIB_ENTRIES),
                "source": "openlist", "fallback": False}
    elif path == "/api/qas/tasks":
        log.append("tasks")
        body = dict(_QAS_TASKS)
    elif path == "/api/library/source":
        body = {"success": True, "source": "openlist", "openlist": True, "openlist_ok": True,
                "openlist_user": "admin", "openlist_message": "",
                "openlist_auth": {"mode": "token", "mode_label": "OpenList 令牌",
                                  "token_set": True, "user_set": False, "password_set": False},
                "openlist_auth_mode": "token", "openlist_auth_source": "token",
                "openlist_auth_label": "OpenList 令牌"}
    else:
        body = {}
    return route.fulfill(status=200, content_type="application/json",
                         body=json.dumps(body, ensure_ascii=False).encode())


def _rows(page):
    return page.locator("#libList .lib-card, #libList .lib-row:not(.lib-head)")


def _open(browser, view, width=1920, height=1080):
    """打开媒体库页，并把布局固定成 card / list。"""
    page = browser.new_page(viewport={"width": width, "height": height})
    page.add_init_script(
        "try{localStorage.setItem('auth_token','t');"
        "localStorage.setItem('lib_view','" + view + "')}catch(e){}")
    page.route("**/*", lambda r: _route(r, []))
    page.goto(URL, wait_until="load", timeout=20000)
    page.evaluate("()=>switchTab('library')")
    page.wait_for_timeout(1200)
    return page


def _click_blank(page, index):
    """点行的空白处（避开勾选框、标题链接与删除按钮）。

    卡片视图里 x=200 大致落在「名称列」上，列表视图里同理落在名称格里 ——
    都不是勾选框的热区，所以这条断言测的是「行点击」这条路。
    """
    _rows(page).nth(index).click(position={"x": 200, "y": 14})


def _focus(page, index):
    """把「当前行」放到第 index 行，并让其行内勾选框拿到焦点。

    为什么不是只调 libFocusRow：空格键要先**到达列表**才有意义 ——
    `_libKeydown` 在焦点落在 INPUT 上时把原生行为交给浏览器（见它的 isCb 分支），
    只调 libFocusRow 时焦点还在 body/上一个元素上，走的路径不一样。
    走查要复现的是「用户用键盘在列表里操作」，所以这里用真实焦点。
    """
    rows = _rows(page)
    rows.nth(index).focus()
    page.evaluate("()=>libFocusRow(%d)" % index)
    page.wait_for_timeout(80)


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

    def fail(label, msg):
        problems.append("[{}] {}".format(label, msg))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"], **_launch_kwargs())
            for view in ("card", "list"):
                label = "媒体库-{}视图".format("卡片" if view == "card" else "列表")
                page = _open(browser, view)
                rows = _rows(page)
                n_rows = page.evaluate(PROBE)["rows"]
                report.append({"label": label, "rows": n_rows})
                if n_rows < 3:
                    fail(label, "媒体库未渲染出行（{} 行），走查无意义".format(n_rows))
                    page.close()
                    continue

                def probe(tag, expect_sel, expect_checked):
                    """取一次状态并断言「逻辑 = 屏幕 = 预期」，返回当前条数。"""
                    m = page.evaluate(PROBE)
                    if m["sel"] != expect_sel or m["checked"] != expect_checked:
                        fail(label, "{}：选中态不一致（逻辑 {} / 屏幕 {} / 期望 {}、{}）"
                             .format(tag, m["sel"], m["checked"], expect_sel, expect_checked))
                    # 「已选 N 项」文案与删除按钮可用性必须跟着状态走
                    if expect_sel and "已选 {} 项".format(expect_sel) not in m["count"]:
                        fail(label, "{}：操作条计数没跟上（{!r}）".format(tag, m["count"]))
                    if not expect_sel and m["count"]:
                        fail(label, "{}：已清空但操作条仍显示 {!r}".format(tag, m["count"]))
                    if bool(m["delDisabled"]) == bool(expect_sel):
                        fail(label, "{}：「删除所选」按钮状态与选中数不符（disabled={}）"
                             .format(tag, m["delDisabled"]))
                    return m["sel"]

                # 1. 点勾选框：勾上 → 再点 → 必须取消（issue #68 的原始复现路径）
                rows.nth(0).locator("input[type=checkbox]").click(force=True)
                probe("点勾选框（第一次）", 1, 1)
                rows.nth(0).locator("input[type=checkbox]").click(force=True)
                probe("再点勾选框（取消）", 0, 0)
                # 第三条也要能取消：不是「第一行特例」
                rows.nth(0).locator("input[type=checkbox]").click(force=True)
                rows.nth(2).locator("input[type=checkbox]").click(force=True)
                probe("勾选两条", 2, 2)
                rows.nth(2).locator("input[type=checkbox]").click(force=True)
                probe("取消其中一条（另一条保留）", 1, 1)
                rows.nth(0).locator("input[type=checkbox]").click(force=True)
                probe("清空最后一条", 0, 0)

                # 2. 点行的空白处：同样要能切换
                _click_blank(page, 1)
                probe("点行空白处（选中）", 1, 1)
                _click_blank(page, 1)
                probe("再点行空白处（取消）", 0, 0)

                # 3. Ctrl/⌘+点击：逐个增减（改动前就成立，留作护栏）
                _click_blank(page, 1)
                probe("普通点第一次", 1, 1)
                page.keyboard.down("Control")
                _rows(page).nth(3).click(position={"x": 200, "y": 14}, modifiers=["Control"])
                page.keyboard.up("Control")
                probe("Ctrl+点击另一行（增选）", 2, 2)
                _rows(page).nth(3).click(position={"x": 200, "y": 14}, modifiers=["Control"])
                probe("Ctrl+点击同一行（减选）", 1, 1)
                _click_blank(page, 1)
                probe("清空", 0, 0)

                # 4. 空格键：焦点在行上时切换当前行。
                # 先显式清一次：上一步（Ctrl+点击）会留下锚点，不清的话
                # 下面的 Shift 连选会从「上一次的锚点」起算，断言就成了错的。
                page.evaluate("()=>libClearSelection()")
                _focus(page, 1)
                page.keyboard.press("Space")
                probe("空格（选中当前行）", 1, 1)
                page.keyboard.press("Space")
                probe("再按空格（取消当前行）", 0, 0)

                # 5. Ctrl+A 全选 → Esc 清空（全选仍只勾可见行）
                page.keyboard.press("Control+a")
                page.wait_for_timeout(150)
                probe("Ctrl+A 全选", n_rows, n_rows)
                page.keyboard.press("Escape")
                page.wait_for_timeout(150)
                probe("Esc 清空", 0, 0)

                # 6. Shift+连选：可扩可缩（本轮改的是同一条函数，顺带护栏）
                page.evaluate("()=>libClearSelection()")
                _focus(page, 0)
                page.keyboard.press("Space")
                page.keyboard.down("Shift")
                page.keyboard.press("ArrowDown" if view == "list" else "ArrowRight")
                page.keyboard.press("ArrowDown" if view == "list" else "ArrowRight")
                page.keyboard.up("Shift")
                probe("Shift 连选 3 行", 3, 3)
                page.keyboard.down("Shift")
                page.keyboard.press("ArrowUp" if view == "list" else "ArrowLeft")
                page.keyboard.up("Shift")
                probe("Shift 收回 1 行", 2, 2)
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
            print("{:<16} 行数 {}".format(m["label"], m["rows"]))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p_ in problems:
                print("  - " + p_)
            return 1
        print("媒体库选中/取消选中走查通过（0 处问题）")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
