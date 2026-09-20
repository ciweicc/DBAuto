#!/usr/bin/env python3
"""顶栏「资源搜索」框快捷键的 DOM 走查（issue #78：「这个搜索框的快捷键是无效的」）。

用户诉求原文：顶栏搜索框的截图 + 一句「这个搜索框的快捷键是无效的」。
截图里那个框的 placeholder 当年写着「搜索资源…（⌘K 命令面板）」——
提示长在搜索框上，用户自然读成「这个框的快捷键 = ⌘K」，
但 ⌘K 当时打开的是**另一个东西**（覆盖全屏的命令面板）。

这类问题的判定只能在渲染后做：
  - 静态扫描能证明「代码里有 ⌘K 分支」，但证明不了「按下去之后焦点真的落在
    搜索框上、面板真的没被打开、输入框里真的没有多出一个 k」；
  - 也证明不了「徽标在窄屏/有输入/聚焦时是否按设计让位」。

断言清单（每条都同时看**逻辑状态**与**屏幕状态**）：
  1. 搜索框内有**可见**的快捷键徽标，且不再有「⌘K 命令面板」这句误导文案
  2. 徽标按平台取值：Windows/Linux 显示 Ctrl+K，macOS 显示 ⌘K
  3. Windows/Linux 下按 Ctrl+K  → 焦点进搜索框，命令面板**不开**
  4. macOS 下按 ⌘K              → 同上（⌘ 在 macOS 上才是本位键）
  5. 焦点已在搜索框内时再按       → 仍是聚焦搜索框，且输入框里**没有多出 k**
  6. 焦点在别的输入框里时按       → 跳到搜索框（不是原地打出 k）
  7. 窄视口下搜索框默认 display:none → 按快捷键必须先打开移动搜索层再聚焦，
     否则「聚焦到一个看不见的输入框」= 按了没反应
  8. 命令面板仍可打开（Ctrl/⌘+/）→ 换键位不等于删功能
  9. 徽标在聚焦 / 有输入时让位（视觉遮挡检查，用 opacity/visibility 判定）

依赖：playwright + chromium（与其它走查脚本同依赖）。
无依赖 / 无产物时以退出码 0 跳过（与其它走查脚本一致）。

用法：
  python3 scripts/check_search_kbd_dom.py            # 检查（失败非零退出）
  python3 scripts/check_search_kbd_dom.py --json     # 机器可读报告
  python3 scripts/check_search_kbd_dom.py --serve    # 自动起本地静态服务
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
# 端口由内核分配（见 _start_server），URL 在拿到实际端口后再算
PORT = 0
URL = ""

UA_WIN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
UA_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

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

# 渲染后的度量：徽标几何 / 可见性 / 焦点 / 面板状态 / 输入框内容，全是屏幕事实。
MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const kbd = q('.search-kbd');
  const input = q('#searchInput');
  const wrap = q('.search-wrapper');
  const pal = q('#cmdPalette');
  const cs = el => el ? getComputedStyle(el) : null;
  const rect = el => { if(!el) return null; const r = el.getBoundingClientRect();
    return {w: Math.round(r.width), h: Math.round(r.height),
            x: Math.round(r.x), y: Math.round(r.y)}; };
  const kcs = cs(kbd);
  return {
    hasKbd: !!kbd,
    kbdText: kbd ? kbd.textContent.trim() : '',
    kbdRect: rect(kbd),
    kbdOpacity: kcs ? kcs.opacity : '',
    kbdVisibility: kcs ? kcs.visibility : '',
    placeholder: input ? input.placeholder : '',
    inputTitle: input ? input.title : '',
    ariaShortcuts: input ? input.getAttribute('aria-keyshortcuts') : null,
    wrapperDisplay: wrap ? cs(wrap).display : '',
    inputRect: rect(input),
    activeId: (document.activeElement && document.activeElement.id) || '',
    activeTag: (document.activeElement && document.activeElement.tagName) || '',
    inputValue: input ? input.value : null,
    paletteShown: pal ? pal.classList.contains('show') : null,
    paletteVisibility: pal ? cs(pal).visibility : '',
  };
}
"""


def _launch_kwargs():
    """本机 playwright 版本与已装 chromium 目录不匹配时，回退到显式可执行文件。"""
    for cand in sorted(glob.glob("/opt/ms-playwright/chromium-*/chrome-linux64/chrome")):
        if os.path.isfile(cand):
            return {"executable_path": cand}
    return {}


def _start_server():
    """起一个本地静态服务。

    端口用 0 让内核分配：本脚本会被反复手工运行，固定端口遇到上一次残留的
    服务（或别的走查脚本）时会安静地**连到旧产物上**，跑出「这轮明明修了却
    还是 FAIL / 明明没修却 PASS」的假象 —— 实测踩到过一次 200ms 的抖动失败，
    就是连到了上一轮的实例。
    """
    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(_QuietHandler, directory=os.path.join(ROOT, "static"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _kbd_hidden(st):
    """徽标是否「已经让位」（不可见）。

    opacity 是带 transition 的：按下键之后有一小段动画，采样到 0.0004 这种
    中间值属于正常渲染，不是「没让位」。这里按「接近 0」判定，
    否则走查会因为一次动画抖动而随机红 —— 实测踩到过。
    """
    fade = st.get("kbdOpacity")
    try:
        faded = fade in ("", None) or float(fade) < 0.05
    except (TypeError, ValueError):
        faded = False
    return faded or st.get("kbdVisibility") == "hidden"


def _route(route):
    url = route.request.url
    if "/api/" not in url:
        return route.fallback()
    # 只取 path：不依赖端口是否被写死在 URL 里
    path = urllib.parse.urlparse(url).path
    if path.rstrip("/").endswith("/api/sse"):
        return route.fulfill(status=200, content_type="text/event-stream", body=b"")
    body = STUBS.get(path, {})
    return route.fulfill(status=200, content_type="application/json",
                         body=json.dumps(body, ensure_ascii=False).encode())


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
        PORT = 8147
        URL = "http://127.0.0.1:{}/index_new.html".format(PORT)
    problems, report = [], []

    def ok(name, cond, detail=""):
        report.append({"check": name, "pass": bool(cond), "detail": detail})
        if not cond:
            problems.append("{}：{}".format(name, detail))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"], **_launch_kwargs())

            def open_page(width=1440, height=900, ua=UA_WIN):
                page = browser.new_page(viewport={"width": width, "height": height},
                                        user_agent=ua)
                # 认证桩：登录页会拦截未带 token 的访问
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", lambda r: _route(r))
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(1200)
                return page

            def ms(page):
                return page.evaluate(MEASURE)

            def blur(page):
                page.evaluate("()=>{try{document.activeElement.blur();}catch(e){}}")
                page.wait_for_timeout(80)

            def press(page, key):
                page.keyboard.press(key)
                page.wait_for_timeout(250)

            # ---------------- Windows / Linux ----------------
            page = open_page()
            st = ms(page)

            ok("搜索框内有可见的快捷键徽标", st["hasKbd"],
               "缺少 .search-kbd（提示埋回 placeholder 就等于没提示）")
            ok("徽标不可见时等于没提示（尺寸 > 0）",
               bool(st["kbdRect"]) and st["kbdRect"]["w"] > 0 and st["kbdRect"]["h"] > 0,
               "徽标几何 = {}".format(st["kbdRect"]))
            ok("Windows 下徽标显示 Ctrl+K", st["kbdText"] == "Ctrl+K",
               "徽标文案 = {!r}（Windows 键盘上没有 ⌘）".format(st["kbdText"]))
            ok("误导性文案已从 placeholder 移除",
               "⌘K" not in (st["placeholder"] or "") and
               "命令面板" not in (st["placeholder"] or ""),
               "placeholder = {!r}".format(st["placeholder"]))
            ok("搜索框声明了 aria-keyshortcuts",
               "K" in (st["ariaShortcuts"] or ""),
               "aria-keyshortcuts = {!r}".format(st["ariaShortcuts"]))
            # 徽标必须真的落在搜索框内部（不是飘在别处）
            if st["kbdRect"] and st["inputRect"]:
                same_row = abs(st["kbdRect"]["y"] - st["inputRect"]["y"]) <= 12
                right_of = st["kbdRect"]["x"] >= st["inputRect"]["x"]
                ok("徽标位于搜索框内、搜索图标右侧",
                   same_row and right_of,
                   "kbd={} input={}".format(st["kbdRect"], st["inputRect"]))

            # Ctrl+K：从 body 聚焦搜索框，且不打开命令面板
            blur(page)
            press(page, "Control+k")
            st = ms(page)
            ok("窗口焦点在 body 时 Ctrl+K 聚焦搜索框",
               st["activeId"] == "searchInput",
               "activeId = {!r}".format(st["activeId"]))
            ok("按 Ctrl+K 不得打开命令面板（那是另一个东西）",
               st["paletteShown"] is False,
               "paletteShown = {!r}".format(st["paletteShown"]))
            ok("聚焦后输入框内容为空（没有把 k 打进去）",
               st["inputValue"] == "",
               "inputValue = {!r}".format(st["inputValue"]))
            # 让位是带 transition 的，等动画走完再采样，避免采到中间帧
            page.wait_for_timeout(350)
            st = ms(page)
            ok("聚焦时徽标让位（不遮挡输入）",
               _kbd_hidden(st),
               "opacity={} visibility={}".format(st["kbdOpacity"], st["kbdVisibility"]))

            # 焦点已在搜索框里时再按：仍聚焦搜索框，且不出字面 k
            page.evaluate("()=>{const i=document.getElementById('searchInput');i.value='';i.focus();}")
            page.wait_for_timeout(80)
            press(page, "Control+k")
            st = ms(page)
            ok("焦点已在搜索框内时 Ctrl+K 仍生效（焦点不跑）",
               st["activeId"] == "searchInput", "activeId = {!r}".format(st["activeId"]))
            ok("焦点在搜索框内按 Ctrl+K 不得打出字面 k",
               st["inputValue"] == "", "inputValue = {!r}".format(st["inputValue"]))

            # 焦点在别的输入框里时按：跳到搜索框
            # 用一个临时的真实输入框做靶子，避免依赖某个页面恰好有 text 输入
            page.evaluate(
                "()=>{var d=document.createElement('input');d.type='text';d.id='__probe';"
                "document.body.appendChild(d);d.focus();}")
            page.wait_for_timeout(80)
            press(page, "Control+k")
            st = ms(page)
            ok("焦点在别的输入框里时 Ctrl+K 跳到搜索框",
               st["activeId"] == "searchInput",
               "activeId = {!r}".format(st["activeId"]))
            ok("原输入框里不得留下字面 k",
               page.evaluate("()=>document.getElementById('__probe').value") == "",
               "probe value = {!r}".format(
                   page.evaluate("()=>document.getElementById('__probe').value")))
            page.evaluate("()=>{var e=document.getElementById('__probe');if(e)e.remove();}")

            # 命令面板仍有入口（换键位 ≠ 删功能）
            blur(page)
            press(page, "Control+/")
            st = ms(page)
            ok("Ctrl+/ 仍能打开命令面板",
               st["paletteShown"] is True,
               "paletteShown = {!r}（命令面板不该被删掉）".format(st["paletteShown"]))
            press(page, "Escape")

            # 徽标在「有输入」时让位
            page.evaluate("()=>{const i=document.getElementById('searchInput');"
                          "i.value='测试';i.dispatchEvent(new Event('input',{bubbles:true}));}")
            page.wait_for_timeout(350)
            st = ms(page)
            ok("有输入时徽标让位",
               _kbd_hidden(st),
               "opacity={} visibility={} boxClass={}".format(
                   st["kbdOpacity"], st["kbdVisibility"],
                   page.evaluate("()=>document.getElementById('searchBox').className")))
            page.close()

            # ---------------- macOS ----------------
            mac = open_page(ua=UA_MAC)
            st = ms(mac)
            ok("macOS 下徽标显示 ⌘K", st["kbdText"] == "⌘K",
               "徽标文案 = {!r}".format(st["kbdText"]))
            blur(mac)
            press(mac, "Meta+k")
            st = ms(mac)
            ok("macOS 下 ⌘K 聚焦搜索框",
               st["activeId"] == "searchInput", "activeId = {!r}".format(st["activeId"]))
            ok("macOS 下 ⌘K 不打开命令面板",
               st["paletteShown"] is False,
               "paletteShown = {!r}".format(st["paletteShown"]))
            mac.close()

            # ---------------- 窄视口（搜索框默认隐藏） ----------------
            narrow = open_page(width=390, height=844)
            st = ms(narrow)
            ok("窄视口下搜索框默认隐藏（前置条件）",
               st["wrapperDisplay"] == "none",
               "wrapperDisplay = {!r}（若已可见，本轮断言无意义）".format(st["wrapperDisplay"]))
            blur(narrow)
            press(narrow, "Control+k")
            st = ms(narrow)
            ok("窄视口按 Ctrl+K 先把搜索层打开",
               st["wrapperDisplay"] != "none",
               "wrapperDisplay = {!r}（聚焦到 display:none 的框 = 按了没反应）".format(
                   st["wrapperDisplay"]))
            ok("窄视口按 Ctrl+K 后搜索框真的有几何尺寸",
               bool(st["inputRect"]) and st["inputRect"]["w"] > 0,
               "inputRect = {}".format(st["inputRect"]))
            ok("窄视口按 Ctrl+K 后焦点进搜索框",
               st["activeId"] == "searchInput", "activeId = {!r}".format(st["activeId"]))
            narrow.close()

            browser.close()
    finally:
        if httpd:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "checks": report},
                         ensure_ascii=False, indent=2))
    else:
        for r in report:
            print("{} {}".format("PASS" if r["pass"] else "FAIL", r["check"]))
            if not r["pass"]:
                print("      " + r["detail"])
        print("---- 共 {} 项，失败 {} 项".format(len(report), len(problems)))

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
