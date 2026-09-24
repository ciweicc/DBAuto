#!/usr/bin/env python3
"""媒体库「网盘容量」DOM 度量走查（issue #101：「媒体库显示网盘容量」）。

用途：
  在真实浏览器里加载构建产物、注入确定性的容量桩数据，对**只有渲染后才能量化**
  的缺陷做断言。容量这块最容易出的不是「读不到」，而是「读到一个看起来像真的
  的错数字」，所以这里钉住三条：

  1. **真的画出来了**：进度条、数字、百分比三件套都在，且数值与桩一致
     （能读 1000/250 就要显示 25.0%，不能显示 0% 或空白）；
  2. **数值坏了要能被看出来**：percent 越界（>100 / <0 / 非数字）时必须夹住，
     不许画出越界宽度的进度条；进度条宽度必须与 percent 自洽（±2px）；
  3. **三档配色按数值走**：≥90% 危险、≥75% 警示、其余正常 —— 颜色是用户
     判断「还能不能存」的第一信号，档位错位等于把这个信号给错了。

  外加两条降级断言：
  4. `known:false`（驱动不报容量）时显示「容量未知」且**不残留上一轮数字**
     —— 陈旧数字比没有数字更危险；
  5. 窄屏（触屏）下容量让位给面包屑，不制造横向溢出。

依赖：playwright + chromium（与 check_library_dom.py 同依赖）。
无依赖时以退出码 0 跳过（不阻断 CI）。

用法：
  python3 scripts/check_library_capacity_dom.py            # 检查（失败非零退出）
  python3 scripts/check_library_capacity_dom.py --json     # 机器可读报告
  python3 scripts/check_library_capacity_dom.py --serve    # 自动起本地静态服务
"""
import argparse
import functools
import http.server
import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "static", "index_new.html")
PORT = 8133
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# ---- 桩数据 ---------------------------------------------------------------
# 容量桩由用例覆盖（见 CASES）：同一份页面要在不同容量下各跑一遍，
# 因此储存在模块级变量里，由 _route 读取。
_CAPACITY = {"success": True, "known": True, "total": 1000, "used": 250,
             "free": 750, "percent": 25.0, "source": "openlist", "message": ""}

_ENTRIES = [
    {"fid": "d1", "name": "影视", "dir": True, "size": 0, "updated_at": 1758000000},
    {"fid": "f1", "name": "示例作品.mkv", "dir": False,
     "size": 2 * 1024 ** 3, "updated_at": 1758000100},
]

STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/library/source": {"success": True, "source": "openlist", "openlist": True,
                            "openlist_ok": True, "openlist_user": "admin",
                            "openlist_message": "", "openlist_auth": {"mode": "token"},
                            "openlist_auth_mode": "token", "openlist_auth_label": "OpenList 令牌"},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.2.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/tmdb/list": {"items": []},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/transfer/status": {"running": False, "progress": [], "stats": {}},
    "/api/qas/tasks": {"success": True, "tasks": []},
}

MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const el = q('#libStorage');
  const r = e => e ? e.getBoundingClientRect() : null;
  const bar = el ? el.querySelector('.lib-cap-bar') : null;
  const fill = el ? el.querySelector('.lib-cap-fill') : null;
  const txt = el ? el.querySelector('.lib-cap-txt') : null;
  const st = el ? getComputedStyle(el) : null;
  const inCrumb = (function(){
    const crumb = q('#libBreadcrumb');
    if(!el || !crumb) return false;
    if(r(el).height === 0) return false;
    return Math.abs(r(el).top - r(crumb).top) < 12;
  })();
  return {
    exists: !!el,
    visible: !!(el && st.display !== 'none' && r(el).height > 0 && r(el).width > 0),
    cls: el ? el.className : '',
    text: txt ? (txt.textContent || '').trim() : '',
    title: txt ? (txt.getAttribute('title') || '') : '',
    ariaNow: bar ? (bar.getAttribute('aria-valuenow') || '') : '',
    ariaLabel: bar ? (bar.getAttribute('aria-label') || '') : '',
    barW: bar ? Math.round(r(bar).width) : 0,
    fillW: fill ? Math.round(r(fill).width) : 0,
    fillColor: fill ? getComputedStyle(fill).backgroundColor : '',
    inCrumb: inCrumb,
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
  };
}
"""

VIEWPORTS = [
    (1440, 900, "1440x900"),
    (390, 844, "390x844 移动端"),
]


def _launch_kwargs():
    import glob
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


def _route(route, requests_log):
    if "/api/" not in route.request.url:
        return route.fallback()
    path = route.request.url.split(str(PORT), 1)[-1].split("?", 1)[0]
    if path.rstrip("/").endswith("/api/sse"):
        return route.fulfill(status=200, content_type="text/event-stream", body=b"")
    if path == "/api/qas/library":
        import urllib.parse
        qp = route.request.url.split("path=", 1)[1] if "path=" in route.request.url else "/"
        qp = urllib.parse.unquote(qp.split("&")[0])
        body = {"success": True, "path": qp, "entries": _ENTRIES,
                "source": "openlist", "fallback": False}
    elif path == "/api/openlist/storage":
        requests_log.append(path)
        body = dict(_CAPACITY)
    else:
        body = STUBS.get(path, {})
    return route.fulfill(status=200, content_type="application/json",
                         body=json.dumps(body, ensure_ascii=False).encode())


# 用例：容量桩 → 期望（档位类名、是否 known、期望百分比文本片段）
CASES = [
    ("正常 25%", {"success": True, "known": True, "total": 1000, "used": 250,
                  "free": 750, "percent": 25.0, "source": "openlist", "message": ""},
     "lib-cap-ok", True, "25.0%"),
    ("警示 80%", {"success": True, "known": True, "total": 1000, "used": 800,
                  "free": 200, "percent": 80.0, "source": "openlist", "message": ""},
     "lib-cap-warn", True, "80.0%"),
    ("危险 95%", {"success": True, "known": True, "total": 1000, "used": 950,
                  "free": 50, "percent": 95.0, "source": "openlist", "message": ""},
     "lib-cap-danger", True, "95.0%"),
    # 坏数据：percent 越界必须夹住（前端 _renderLibStorage 里 clamp 到 [0,100]）
    ("越界 180%", {"success": True, "known": True, "total": 1000, "used": 1800,
                   "free": 0, "percent": 180.0, "source": "openlist", "message": ""},
     "lib-cap-danger", True, "100.0%"),
    ("非数字 percent", {"success": True, "known": True, "total": 1000, "used": 100,
                        "free": 900, "percent": "abc", "source": "openlist", "message": ""},
     "lib-cap-ok", True, "0.0%"),
    # 降级：驱动不报容量
    ("未知（驱动不报容量）", {"success": False, "known": False, "total": 0, "used": 0,
                             "free": 0, "percent": 0, "source": "openlist", "message": ""},
     "lib-cap-unknown", False, "容量未知"),
]


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
            for width, height, label in VIEWPORTS:
                for case_name, capacity, want_cls, want_known, want_text in CASES:
                    tag = "{} / {}".format(label, case_name)
                    _CAPACITY.clear()
                    _CAPACITY.update(capacity)
                    requests_log = []
                    page = browser.new_page(viewport={"width": width, "height": height})
                    page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                    # playwright 会把 (route, request) 两个位置参数一起传给处理器，
                    # 因此第二个形参必须显式接收并**丢弃** —— 用默认值捕获列表会被
                    # request 覆盖（本轮实测踩到：requests_log 变成了 Request）。
                    def _handler(route, _request, _log=requests_log):
                        return _route(route, _log)
                    page.route("**/*", _handler)
                    page.goto(URL, wait_until="load", timeout=20000)
                    page.wait_for_timeout(1200)
                    page.evaluate("()=>switchTab('library')")
                    page.wait_for_timeout(1200)
                    m = page.evaluate(MEASURE)
                    m["label"] = tag
                    m["case"] = case_name
                    report.append(m)

                    def fail(msg):
                        problems.append("[{}] {}".format(tag, msg))

                    narrow = width <= 900
                    if narrow:
                        # 窄屏：容量让位给面包屑（CSS display:none），
                        # 但**不得残留可见的文字** —— 只藏容器不藏文字会被用户看到
                        # 一截溢出/错位的数字。这里同时断言不可见与零横向溢出。
                        if m["visible"]:
                            fail("窄屏下容量仍可见（应与 .lib-keyhint 同断点让位）")
                        if m["docOverflowX"] > 0:
                            fail("窄屏横向溢出 {}px".format(m["docOverflowX"]))
                        page.close()
                        continue

                    # 0. 元素必须存在（存在性先于一切视觉断言）
                    if not m["exists"]:
                        fail("页面缺少 #libStorage 节点（容量功能没落地）")
                        page.close()
                        continue
                    # 1. 必须可见
                    if not m["visible"]:
                        fail("容量未显示（class={!r} text={!r}）".format(m["cls"], m["text"]))
                        page.close()
                        continue
                    # 2. 必须在面包屑同一行（它是页面状态信息，不是独立占一行的块）
                    if not m["inCrumb"]:
                        fail("容量块没有与面包屑同行（会多占一行，把目录往下挤）")
                    # 3. 档位类名必须按数值来
                    if want_cls not in m["cls"]:
                        fail("档位类名不对：期望 {} 实际 {!r}（{}）".format(
                            want_cls, m["cls"], case_name))
                    # 4. 文本要带期望的关键信息
                    if want_text not in m["text"]:
                        fail("容量文案缺少 {!r}（实际 {!r}）".format(want_text, m["text"]))
                    # 5. 进度条宽度要与 percent 自洽（±2px，抗亚像素）
                    if want_known:
                        want_pct = float(want_text.rstrip('%')) if want_text.endswith('%') else None
                        if want_pct is not None:
                            expect_w = m["barW"] * want_pct / 100.0
                            if abs(m["fillW"] - expect_w) > 2:
                                fail("进度条填充宽度与百分比不自洽："
                                     "期望≈{:.0f}px 实际 {}px（bar {}px，{}%）".format(
                                         expect_w, m["fillW"], m["barW"], want_pct))
                        # aria 值必须给出（无障碍 + 走查可读）
                        if not m["ariaNow"]:
                            fail("进度条缺少 aria-valuenow")
                        if not m["title"]:
                            fail("容量缺少 title（鼠标悬停看不到「剩余多少」）")
                        # 填充色必须真的上了色（不是透明 / 与底色相同）
                        if not m["fillColor"] or m["fillColor"] in ("rgba(0, 0, 0, 0)", "transparent"):
                            fail("进度条填充色是透明（条子画了但看不见）")
                    else:
                        # 未知态不该有进度条（画一条 0% 的条会被读成「盘空了」）
                        if m["barW"] > 0:
                            fail("「容量未知」时仍画了进度条（会被误读为 0%）")
                    # 6. 横向溢出
                    if m["docOverflowX"] > 0:
                        fail("横向溢出 {}px".format(m["docOverflowX"]))
                    page.close()
            browser.close()
    finally:
        if httpd:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report},
                         ensure_ascii=False, indent=2))
    else:
        for m in report:
            print("{label:<34} cls={cls:<18} text={text!r}".format(
                label=m["label"], cls=m["cls"], text=m["text"]))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p in problems:
                print("  - " + p)
        else:
            print("网盘容量走查通过：{} 组用例 0 处问题".format(len(report)))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
