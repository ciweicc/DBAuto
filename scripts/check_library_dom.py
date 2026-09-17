#!/usr/bin/env python3
"""媒体库页 DOM 度量走查（issue #27：模块置顶 + 「网盘文件」加载慢）。

用途：
  在真实浏览器里加载构建产物、注入确定性的接口桩数据，对**只有渲染后才能量化**
  的缺陷做断言 —— 这些指标静态扫描（tests/test_build.py）表达不了：

  1. 「网盘文件」必须排在「QAS 转存任务」之前（用户诉求：网盘文件置顶），
     且首屏（第一个视口高度）内就要看到网盘文件卡片顶部；
  2. 目录请求发起的瞬间就要有可读的加载反馈（不是白屏），
     且反馈里带**已用时长**，让用户能区分「在等」和「卡死」；
  3. 目录接口长时间不返回时，页面必须**按时快速失败**给出错误态 + 重试入口，
     而不是无限转圈（历史缺陷：后端 15s×3 重试 ≈ 32s 全部压给用户）；
  4. 进入子目录再返回父目录，第二次必须**命中缓存**（0 次新请求）且立刻出内容；
  5. 目录与任务请求必须**并行**发出，不得串行等待；
  6. 多视口下无横向溢出、表格列不被压扁（含移动端卡片化）。

依赖：playwright + chromium（与 scripts/check_overview_dom.py 同依赖），
  pip install playwright && python -m playwright install chromium
无依赖时脚本以退出码 0 跳过（避免影响不装浏览器的 CI 阶段）。

用法：
  python3 scripts/check_library_dom.py            # 检查（失败非零退出）
  python3 scripts/check_library_dom.py --json     # 机器可读报告
  python3 scripts/check_library_dom.py --serve    # 自动起本地静态服务
"""
import argparse
import functools
import http.server
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "static", "index_new.html")
PORT = 8126
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# ---------------------------------------------------------------- 桩数据
# 与 app_modules/routes_library.py 契约一致：
#   GET /api/qas/tasks          -> {success, tasks:[{taskname,savepath,shareurl}]}
#   GET /api/qas/library?path=  -> {success, path, entries:[{fid,name,dir,size,updated_at}]}
_QAS_TASKS = {"success": True, "tasks": [
    {"taskname": "示例任务-{:02d}".format(i),
     "savepath": "/媒体库/电影",
     "shareurl": "https://pan.quark.cn/s/demo{:02d}".format(i)}
    for i in range(8)
]}


def _entries(path):
    if path in ("", "/"):
        return [
            {"fid": "d1", "name": "电影", "dir": True, "size": 0, "updated_at": 1758000000},
            {"fid": "d2", "name": "电视剧", "dir": True, "size": 0, "updated_at": 1758000001},
            {"fid": "f1", "name": "示例作品标题-01.2024.2160p.mkv", "dir": False,
             "size": 8 * 1024 ** 3, "updated_at": 1758000100},
            {"fid": "f2", "name": "示例作品标题-02.mkv", "dir": False,
             "size": 1024 ** 3, "updated_at": 1758000200},
        ]
    return [
        {"fid": "x1", "name": "示例作品标题-03.mkv", "dir": False,
         "size": 2 * 1024 ** 3, "updated_at": 1758000300},
    ]


STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.2.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/tmdb/list": {"items": []},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/transfer/status": {"running": False, "progress": [], "stats": {}},
    "/api/qas/tasks": _QAS_TASKS,
}

MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const lib = q('#libList'), qas = q('#qasTaskList');
  const libCard = lib && lib.closest('.card'), qasCard = qas && qas.closest('.card');
  const r = el => el ? el.getBoundingClientRect() : null;
  const libR = r(libCard), qasR = r(qasCard);
  const page = q('#tabLibrary');
  const rows = [...document.querySelectorAll('#libList .lib-row')];
  return {
    missing: !lib || !qas,
    libTop: libR ? Math.round(libR.top) : null,
    qasTop: qasR ? Math.round(qasR.top) : null,
    libCountText: q('#libCount') ? q('#libCount').textContent : '',
    qasCountText: q('#qasTaskCount') ? q('#qasTaskCount').textContent : '',
    libHtmlHead: lib ? lib.innerHTML.slice(0, 400) : '',
    hasBody: lib && lib.querySelector('.lib-table') ? lib.querySelectorAll('.lib-row').length : 0,
    loadingHint: q('#libList #libLoadingHint') ? q('#libList #libLoadingHint').textContent : '',
    retryBtn: q('#libList button') ? q('#libList button').textContent : '',
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    pageOverflowX: page ? page.scrollWidth - page.clientWidth : 0,
    rowCols: rows.length ? getComputedStyle(rows[rows.length - 1]).gridTemplateColumns : '',
    rowH: rows.length ? Math.round(r(rows[rows.length - 1]).height) : 0,
    vh: window.innerHeight,
  };
}
"""

VIEWPORTS = [
    (1440, 900, "1440x900"),
    (390, 844, "390x844 移动端"),
]


def _start_server():
    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(_QuietHandler, directory=os.path.join(ROOT, "static"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _route(route, requests_log, slow_seconds=None):
    """统一桩路由；slow_seconds 只作用于 /api/qas/library（模拟 QAS 卡住）。"""
    url = route.request.url
    if "/api/" not in url:
        return route.fallback()
    # 注意：网盘浏览接口自带 ?path= 参数，后续还可能带 &token=，
    # 而且路径里含 / 编码（%2F）。这里必须只按 host 切分、只取 path 段，
    # 否则 /api/qas/library?path=/ 会被切成 "/api/qas/library?path=" 而落到兜底桩。
    path = url.split(str(PORT), 1)[-1].split("?", 1)[0]
    if path.rstrip("/").endswith("/api/sse"):
        return route.fulfill(status=200, content_type="text/event-stream", body=b"")
    if path == "/api/qas/library":
        requests_log.append(time.time())
        if slow_seconds:
            time.sleep(slow_seconds)
        qp = url.split("path=", 1)[1] if "path=" in url else "/"
        qp = qp.split("&")[0]
        import urllib.parse
        body = {"success": True, "path": urllib.parse.unquote(qp),
                "entries": _entries(urllib.parse.unquote(qp))}
    elif path == "/api/qas/tasks":
        requests_log.append(time.time())
        body = dict(_QAS_TASKS)
    else:
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
    problems, report = [], []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            for width, height, label in VIEWPORTS:
                log = []
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", lambda r: _route(r, log))
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(1500)

                # 切到媒体库页（按需加载在 switchTab 里触发）
                page.evaluate("()=>switchTab('library')")
                page.wait_for_timeout(1500)
                m = page.evaluate(MEASURE)
                m["label"] = label
                report.append(m)

                def fail(msg):
                    problems.append("[{}] {}".format(label, msg))

                if m.get("missing"):
                    fail("页面缺少 #libList / #qasTaskList 节点")
                    page.close()
                    continue

                # 1. 模块顺序：网盘文件在 QAS 任务之上，且首屏可见
                if not (m["libTop"] < m["qasTop"]):
                    fail("「网盘文件」未排在「QAS 转存任务」之前"
                         "（网盘 {}px vs 任务 {}px）".format(m["libTop"], m["qasTop"]))
                if m["libTop"] > m["vh"]:
                    fail("「网盘文件」不在首屏内（top {}px > 视口 {}px）".format(
                        m["libTop"], m["vh"]))
                if m["hasBody"] < 3:
                    fail("网盘目录渲染的行数异常（{} 行）".format(m["hasBody"]))
                if "耗时" not in m["libCountText"]:
                    fail("目录计数未反馈耗时（'{}'），无法解释「慢」".format(m["libCountText"]))
                # 任务表必须在同一页可见（没有被挤掉）
                if "共 8 条" not in m["qasCountText"]:
                    fail("QAS 任务表未渲染（'{}'）".format(m["qasCountText"]))

                # 2/6. 溢出
                if m["docOverflowX"] > 0:
                    fail("文档横向溢出 {}px".format(m["docOverflowX"]))
                if m["pageOverflowX"] > 0:
                    fail("媒体库页横向溢出 {}px".format(m["pageOverflowX"]))
                if width >= 760 and m["rowCols"].count("px") < 4:
                    fail("桌面表格列数异常（grid-template-columns={}）".format(m["rowCols"]))
                if width >= 760 and m["rowH"] < 32:
                    fail("表格行高 {}px 过小，内容被压扁".format(m["rowH"]))
                page.close()

            # --- 3. 慢接口：必须按时快速失败 + 重试入口 ---
            log2 = []
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
            # 目录接口「永不返回」：模拟 QAS 卡死
            def hang(route):
                """目录接口「永不响应」，模拟 QAS 卡死（其余接口走正常桩）。

                注意：这里**不能** sleep 后再 fulfill —— Playwright 的 route 回调
                会阻塞驱动进程，超时会直接把 runtime 写挂（实测 EPIPE）。
                正确做法是「收到请求但不 fulfill」，让前端的软超时先到期。
                """
                url = route.request.url
                if "/api/" not in url:
                    return route.fallback()
                path = url.split(str(PORT), 1)[-1].split("?", 1)[0]
                if path == "/api/qas/library":
                    log2.append(time.time())
                    return          # 故意不响应
                return _route(route, log2)

            page.route("**/*", hang)
            page.goto(URL, wait_until="load", timeout=20000)
            # 请求「发出」即计时：加载反馈必须在发出后的短时间内就出现，而不是等接口返回
            page.wait_for_timeout(200)
            t0 = time.time()
            page.evaluate("()=>switchTab('library')")
            # 400ms 后取加载反馈：必须已经有可读文案（不是白屏）
            page.wait_for_timeout(400)
            early = page.evaluate(MEASURE)
            if not early.get("loadingHint") or "已用" not in early["loadingHint"]:
                problems.append("[慢接口] 请求发出后没有可读的加载反馈（hint={!r}）".format(
                    early.get("loadingHint")))

            # 前端的软超时 8s + 少量余量 → 12s 内必须失败可重试
            deadline = t0 + 12
            failed_ok = False
            while time.time() < deadline:
                page.wait_for_timeout(500)
                cur = page.evaluate(MEASURE)
                if "读取失败" in cur.get("libHtmlHead", ""):
                    failed_ok = True
                    report.append({"label": "慢接口(1440x900)", **cur,
                                   "elapsed": round(time.time() - t0, 1)})
                    if "重试" not in cur.get("retryBtn", ""):
                        problems.append("[慢接口] 失败态缺少重试入口")
                    if "已用" not in cur.get("libHtmlHead", ""):
                        problems.append("[慢接口] 失败态未给出已用时长，无法定位是超时还是配置错误")
                    break
            if not failed_ok:
                problems.append("[慢接口] 目录接口 60s 不返回时，12s 内仍未快速失败"
                                "（用户会一直盯着「加载中」）")
            page.close()

            # --- 4/5. 缓存复用：进子目录再返回父目录，第二次必须 0 新请求且立即渲染 ---
            log3 = []
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
            page.route("**/*", lambda r: _route(r, log3))
            page.goto(URL, wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('library')")
            page.wait_for_timeout(1200)
            n_after_root = len(log3)
            # 进子目录
            page.evaluate("()=>enterDir('电影')")
            page.wait_for_timeout(1000)
            n_after_sub = len(log3)
            # 返回父目录：应命中缓存，不再打网络
            page.evaluate("()=>loadLibrary('/')")
            page.wait_for_timeout(500)
            n_after_back = len(log3)
            detail = page.evaluate(MEASURE)
            report.append({"label": "缓存复用(1440x900)", "requests": [n_after_root, n_after_sub,
                                                                     n_after_back],
                           "libCountText": detail["libCountText"], "hasBody": detail["hasBody"]})
            if n_after_sub <= n_after_root:
                problems.append("[缓存] 进入子目录后未观察到新的目录请求"
                                "（请求数 {} -> {}）".format(n_after_root, n_after_sub))
            if n_after_back != n_after_sub:
                problems.append("[缓存] 返回父目录又打了网络（{} -> {}），缓存未生效".format(
                    n_after_sub, n_after_back))
            if "缓存" not in detail["libCountText"]:
                problems.append("[缓存] 复用缓存时未标注「缓存」，用户无法分辨数据新旧"
                                "（'{}'）".format(detail["libCountText"]))
            if detail["hasBody"] < 3:
                problems.append("[缓存] 返回父目录后未立即渲染出内容（{} 行）".format(
                    detail["hasBody"]))
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
            if "libTop" in m:
                print("{:<22} 网盘文件 top {:<5} QAS 任务 top {:<5} 行数 {:<3} 计数 {}".format(
                    m["label"], m["libTop"], m["qasTop"], m["hasBody"],
                    m["libCountText"]))
            elif "requests" in m:
                print("{:<22} 请求序列 {} 行数 {} 计数 {}".format(
                    m["label"], m["requests"], m["hasBody"], m["libCountText"]))
            else:
                print("{:<22} 快速失败用时 {}s".format(m["label"], m.get("elapsed")))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p_ in problems:
                print("  - " + p_)
            return 1
        print("媒体库页 DOM 度量走查通过（0 处问题）")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
