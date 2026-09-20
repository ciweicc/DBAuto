#!/usr/bin/env python3
"""侧边栏实时任务日志 DOM 度量走查（issue #11）。

用途：
  在真实浏览器里加载构建产物，注入确定性的接口桩数据（正在运行 + 长日志），
  对**只有渲染后才能量化**的日志栏缺陷做断言。这些指标静态扫描
  （tests/test_build.py）表达不了：

  1. 打开页面（含窄屏 / 移动端）日志栏必须可见 —— 不得默认折叠成 48px 轨道，
     否则正在运行的任务日志一条都看不到（issue #11 的直接表现）；
  2. 日志区必须自动定位到**最新**一条，且底部提示与滚动位置一致
     （曾出现 scrollTop=0 停在最旧一行，用户误判为「日志不全」）；
  3. 日志区可容纳的条数必须随视口高度增长，不得写死 340px 上限
     （900px 高的屏幕上旧实现只用了约 1/3 屏）；
  4. 窄屏点 FAB / 展开按钮后，日志栏必须真正进入可视区
     （覆盖层抽屉需要 .open，只去掉 .collapsed 仍是屏幕外）；
  5. 超长日志行折行后要有续行缩进（与正文对齐），且不得产生横向溢出；
  6. 暂停滚动必须锁定当前视图并给出可读提示；
  7. 窄屏抽屉必须能从「带折叠记忆缩窄窗口」这条路径打开 —— 否则
     `.collapsed` 残留会把抽屉压回 48px 轨道 / 屏幕外（issue #11 二次复发）；
  8. 超宽屏主列不得留下大片空白带（`max-width:1440px` + `margin:auto`
     曾在 2560px 下空出约 290px，日志栏被顶到最右边）。

依赖：playwright + chromium（与 scripts/check_overview_dom.py 同依赖），
  pip install playwright && python -m playwright install chromium
无依赖时脚本以退出码 0 跳过（避免影响不装浏览器的 CI 阶段）。

用法：
  python3 scripts/check_log_panel_dom.py            # 检查（失败非零退出）
  python3 scripts/check_log_panel_dom.py --json     # 机器可读报告
  python3 scripts/check_log_panel_dom.py --serve    # 自动起本地静态服务
"""
import argparse
import functools
import http.server
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "static", "index_new.html")
PORT = 8124
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# ---------------------------------------------------------------- 桩数据
# 与 app_modules/routes_transfer.py 的 /api/transfer/status 契约一致：
#   {running, progress:[str], stats:{total,ok,skipped,failed,searched,start_time}}
_LOG_LINE = "2026-09-15 10:00:{:02d} 跳过《示例作品标题-{:02d}》：已存在于转存库 (exists) https://pan.quark.cn/s/demo{:02d}"
# 一条必然折行的超长行，用于验证续行缩进
_LONG_LINE = ("2026-09-15 10:01:00 转存《一个名字特别长的示例作品标题"
              "用于验证折行缩进对齐效果》：https://pan.quark.cn/s/verylongsharelinkabcdefghijklmnop")



def _launch_kwargs():
    """本机 playwright 版本与已装 chromium 目录不匹配时回退到显式可执行文件。

    仅影响开发环境的手工走查（CI 上通常直接跳过），不改变断言内容。
    """
    import glob
    for cand in sorted(glob.glob("/opt/ms-playwright/chromium-*/chrome-linux64/chrome")):
        if os.path.isfile(cand):
            return {"executable_path": cand}
    return {}


def _progress():
    lines = [_LOG_LINE.format(i % 60, i, i) for i in range(60)]
    lines.insert(10, _LONG_LINE)
    # issue #68：后端把「主行 + 详情」成对写进 progress（详情是 requests 的整段异常）。
    # 这里插 6 组，用于断言「详情默认折叠、不占视觉行」。
    for i in range(6):
        lines.append("2026-09-15 10:02:{:02d} HTTP POST 超时放弃（已重试 3 次）".format(i))
        lines.append("2026-09-15 10:02:{:02d} HTTP POST 超时放弃（已重试 3 次）: POST https://pansou/api | "
                     "HTTPConnectionPool(host='192.168.5.55', port=8080): Read timed out. (read timeout=20)".format(i))
    return lines


STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.1.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/tmdb/list": {"items": []},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/transfer/status": {
        "running": True,
        "progress": _progress(),
        "stats": {"total": 61, "ok": 1, "skipped": 60, "failed": 0, "searched": 61,
                  "start_time": "2026-09-15 10:00:00"},
    },
}

MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const panel = q('#logPanel'), box = q('#log');
  if (!panel || !box) return {missing: true};
  const lines = [...box.querySelectorAll('.log-line')];
  const visible = lines.filter(e => e.getBoundingClientRect().height > 0);
  const wrapped = lines.filter(e => e.classList.contains('wrapped'));
  const panelW = Math.round(panel.getBoundingClientRect().width);
  const panelLeft = panel.getBoundingClientRect().left;
  const overflow = [...panel.querySelectorAll('*')].filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && Math.round(r.right - (panelLeft + panel.clientWidth)) > 1;
  });
  // 视口内可完整显示的行数（用于证明「可容纳条数随高度增长」）
  const boxRect = box.getBoundingClientRect();
  const inView = lines.filter(e => {
    const r = e.getBoundingClientRect();
    return r.top >= boxRect.top - 1 && r.bottom <= boxRect.bottom + 1;
  }).length;
  // issue #68：默认折叠的详情块不计入「视觉行数」（它们只占 1 行灰字 +
  // 折叠态省略号），展开的才计。用户扫一屏看到的信息量由可见行数决定。
  const details = [...box.querySelectorAll('.log-detail')];
  const collapsedDetails = details.filter(d => d.classList.contains('collapsed'));
  const detailTextH = details.map(d => {
    const t = d.querySelector('.log-detail-text');
    return t ? Math.round(t.getBoundingClientRect().height) : 0;
  });
  return {
    missing: false,
    detailCount: details.length,
    detailCollapsed: collapsedDetails.length,
    detailMaxTextH: detailTextH.length ? Math.max(...detailTextH) : 0,
    panelW,
    collapsed: panel.classList.contains('collapsed'),
    panelVisible: getComputedStyle(panel).display !== 'none' && panelW > 60,
    boxH: Math.round(boxRect.height),
    lineCount: lines.length,
    visibleLines: visible.length,
    inViewLines: inView,
    wrappedCount: wrapped.length,
    wrapPad: wrapped.length ? getComputedStyle(wrapped[0]).paddingLeft : null,
    wrapIndent: wrapped.length ? getComputedStyle(wrapped[0]).textIndent : null,
    atBottom: Math.abs(box.scrollHeight - box.clientHeight - box.scrollTop) < 3,
    panelRight: Math.round(panel.getBoundingClientRect().right),
    vw: window.innerWidth,
    panelInViewport: (() => { const r = panel.getBoundingClientRect();
      return r.width > 100 && r.left < window.innerWidth - 8 && r.right > 8; })(),
    // 主列「内部」的左侧空白带：.content 的左边相对其网格列起点的偏移。
    // 不能用 .app 左边做基准 —— 那会把 200px 侧边栏也算成空白带。
    mainGutterLeft: (() => {
      const c = document.querySelector('.content');
      if (!c) return 0;
      const pr = c.parentElement.getBoundingClientRect();
      const cr = c.getBoundingClientRect();
      return Math.round(cr.left - pr.left);
    })(),
    // 网格第三列（日志栏所在列）的宽度，用于判断超宽屏是否被无限拉宽
    logTrackW: (() => {
      const a = document.querySelector('.app');
      if (!a) return 0;
      const cols = getComputedStyle(a).gridTemplateColumns.split(' ').filter(Boolean);
      return cols.length > 2 ? Math.round(parseFloat(cols[cols.length - 1])) : 0;
    })(),
    overflowsPanel: overflow.length,
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    boxOverflowX: box.scrollWidth - box.clientWidth,
    hint: q('#logHint') ? q('#logHint').textContent : null,
  };
}
"""

# (宽度, 高度, 是否窄屏抽屉, 标签)
VIEWPORTS = [
    (2560, 1392, False, "2560x1392 超宽屏"),
    (1920, 1080, False, "1920x1080 大屏"),
    (1600, 900, False, "1600x900"),
    (1440, 900, False, "1440x900"),
    (1366, 900, False, "1366x900"),
    (1280, 900, False, "1280x900"),
    (1200, 900, True, "1200x900 日志栏转覆盖层"),
    (1100, 900, True, "1100x900 覆盖层"),
    (1024, 768, True, "1024x768 侧栏收起"),
    (390, 844, True, "390x844 移动端"),
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
            for width, height, narrow, label in VIEWPORTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", handler)
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(1100)
                m = page.evaluate(MEASURE)
                m["label"] = label
                m["width"] = width
                m["height"] = height
                report.append(m)

                def fail(msg):
                    problems.append("[{}] {}".format(label, msg))

                if m.get("missing"):
                    fail("页面缺少 #logPanel / #log 节点")
                    page.close()
                    continue

                # 1. 打开页面日志栏必须可见（不得默认折叠）
                if m["collapsed"] or not m["panelVisible"]:
                    fail("打开页面日志栏默认折叠/不可见（折叠={}, 宽={}px）".format(
                        m["collapsed"], m["panelW"]))
                if m["visibleLines"] == 0:
                    fail("日志栏可见但一条日志都没渲染出来")

                # 2. 必须定位到最新一条，且提示与位置一致
                if not m["atBottom"]:
                    fail("日志未定位到最新一条（hint={!r}）".format(m["hint"]))
                if m["hint"] and "最新" not in m["hint"]:
                    fail("底部提示未反映「当前为最新」：{!r}".format(m["hint"]))

                # 3. 高度必须随视口增长，不得写死小上限
                want = min(max(0.40 * height, 300), 900)
                if m["boxH"] < want:
                    fail("日志区高度 {}px 偏小（{}x{} 期望 ≥ {:.0f}px，疑似仍有 340px 硬上限）".format(
                        m["boxH"], width, height, want))

                # 4/5. 折行缩进与横向溢出
                if m["wrappedCount"] == 0:
                    fail("超长日志行未被标记为折行（续行缩进失效）")
                elif m["wrapIndent"] in (None, "0px", "0"):
                    fail("折行续行未缩进（text-indent={}）".format(m["wrapIndent"]))
                if m["docOverflowX"] > 0:
                    fail("文档横向溢出 {}px".format(m["docOverflowX"]))
                if m["boxOverflowX"] > 0:
                    fail("日志区横向溢出 {}px".format(m["boxOverflowX"]))
                if m["overflowsPanel"] > 0:
                    fail("{} 个元素溢出日志栏右边界".format(m["overflowsPanel"]))
                if m["panelW"] > 60 and m["panelW"] < 240:
                    fail("日志栏宽度 {}px 过窄（内容会被挤压）".format(m["panelW"]))

                # 6'. issue #68：requests 异常必须收成「默认折叠的详情」。
                #     判据：详情块存在、默认折叠、且折叠态文字高度只有一行
                #     （展开后该段异常在 400px 宽栏里必然折成 ≥ 3 行）。
                if m.get("detailCount", 0) == 0:
                    fail("面板没有渲染任何折叠详情块（issue #68 的详情行未被认领）")
                elif m["detailCollapsed"] != m["detailCount"]:
                    fail("详情块默认未折叠（{}/{} 折叠）".format(
                        m["detailCollapsed"], m["detailCount"]))
                elif m["detailMaxTextH"] > 24:
                    fail("折叠态详情仍占 {}px 高（应在 1 行内，约 ≤24px）".format(
                        m["detailMaxTextH"]))

                # 6''. 「详情」必须点得开：展开后原文完整可见、不横向溢出。
                #      折叠只是默认态，用户排查问题时仍要能拿到 requests 原文。
                if m["detailCount"] > 0:
                    expand_err = page.evaluate('''() => {
                      const box = document.getElementById('log');
                      const det = box.querySelector('.log-detail.collapsed');
                      if (!det) return 'no-collapsed-detail';
                      const btn = det.querySelector('.log-detail-toggle');
                      if (!btn) return 'no-toggle';
                      btn.click();
                      const t = det.querySelector('.log-detail-text');
                      const br = box.getBoundingClientRect();
                      const tr = t.getBoundingClientRect();
                      const cs = getComputedStyle(t);
                      return {
                        label: btn.textContent,
                        aria: btn.getAttribute('aria-expanded'),
                        stillCollapsed: det.classList.contains('collapsed'),
                        textH: Math.round(tr.height),
                        overflowRight: Math.round(tr.right - (br.right - 8)),
                        overflowX: box.scrollWidth - box.clientWidth,
                        whiteSpace: cs.whiteSpace,
                      };
                    }''')
                    if expand_err == 'no-collapsed-detail' or expand_err == 'no-toggle':
                        fail("折叠详情缺少可点的「详情」按钮（{}）".format(expand_err))
                    else:
                        if expand_err["stillCollapsed"] or expand_err["aria"] != "true":
                            fail("点「详情」后未展开（collapsed={} aria={}）".format(
                                expand_err["stillCollapsed"], expand_err["aria"]))
                        if expand_err["label"] != "收起":
                            fail("展开后按钮文案未变为「收起」：{}".format(expand_err["label"]))
                        if expand_err["whiteSpace"] == "nowrap":
                            fail("展开后详情仍被 nowrap 截断，原文不可读")
                        if expand_err["overflowRight"] > 1 or expand_err["overflowX"] > 0:
                            fail("展开后的详情横向溢出（右溢 {}px / 日志区 {}px）".format(
                                expand_err["overflowRight"], expand_err["overflowX"]))
                        if expand_err["textH"] <= 24:
                            fail("展开后详情仍只有一行（{}px），原文没显示出来".format(
                                expand_err["textH"]))
                    # 收起，避免影响后续视口断言的可复现性
                    page.evaluate("()=>{var d=document.querySelector('#log .log-detail');"
                                  "if(d){var t=d.querySelector('.log-detail-toggle');"
                                  "if(t&&t.textContent==='收起')t.click();}}")
                    m["detailExpandable"] = True

                # 7. 宽屏（常驻栏）日志栏必须真的落在视口内
                #    （窄屏首屏是「关闭的抽屉」，由下方点 FAB 后的断言覆盖）
                if not narrow and not m["panelInViewport"]:
                    fail("日志栏不在视口内（宽 {}px, 位置 ~{}, 视口 {}）".format(
                        m["panelW"], m["panelRight"], m["vw"]))

                # 8. 主列左侧不得出现大片空白带。
                #    根因：.app 第三列 minmax(0,1fr) 在 2560px 下被拉到 2020px，
                #    而 .content{max-width:1440px;margin:0 auto} 只把内容居中，
                #    于是左侧空出约 290px、日志栏被顶到最右边（用户截图的观感）。
                #    判据：主列左侧空白不得超过主列宽度的 1/4（正常情况是 0）。
                if m["logTrackW"] > 0 and m["panelW"] > 0:
                    mainW = m["vw"] - m["logTrackW"] - m["panelW"]
                else:
                    mainW = 0
                if mainW > 0 and m["mainGutterLeft"] > max(40, 0.25 * mainW):
                    fail("主列左侧空白带 {}px（主列宽 {}px，内容被挤到右侧）".format(
                        m["mainGutterLeft"], mainW))

                # 7'. 带折叠记忆打开窄屏页面：抽屉入口必须仍然可用。
                #     这是 issue #11 二次复发的现场 —— 用户曾在宽屏折叠过日志栏，
                #     localStorage 留有 logPanelCollapsed=1，缩窄/直接以窄屏打开时
                #     日志栏被残留的 .collapsed 压回 48px 轨道或屏幕外。
                if narrow:
                    p2 = browser.new_page(viewport={"width": width, "height": height})
                    p2.add_init_script(
                        "try{localStorage.setItem('auth_token','t');"
                        "localStorage.setItem('logPanelCollapsed','1')}catch(e){}")
                    p2.route("**/*", handler)
                    p2.goto(URL, wait_until="load", timeout=20000)
                    p2.wait_for_timeout(900)
                    p2.evaluate("()=>{ var f=document.getElementById('logFab'); if(f) f.click(); }")
                    p2.wait_for_timeout(600)
                    m2 = p2.evaluate(MEASURE)
                    if m2.get("missing"):
                        fail("（折叠记忆用例）缺少 #logPanel / #log 节点")
                    else:
                        if not m2["panelInViewport"]:
                            fail("（折叠记忆用例）点 FAB 后日志栏仍在视口外"
                                 "（折叠={} 宽 {}px 位置 {}~{}）".format(
                                     m2["collapsed"], m2["panelW"], m2["panelW"],
                                     m2["panelRight"]))
                        if not m2["atBottom"]:
                            fail("（折叠记忆用例）展开后未定位到最新一条")
                        if m2["visibleLines"] == 0:
                            fail("（折叠记忆用例）展开后一条日志都没有")
                    p2.close()

                # 4'. 窄屏点 FAB 必须真正把抽屉打开到可视区
                if narrow:
                    page.evaluate("()=>{ var f=document.getElementById('logFab'); if(f) f.click(); }")
                    page.wait_for_timeout(700)
                    after = page.evaluate(MEASURE)
                    if after.get("missing"):
                        fail("窄屏点 FAB 后缺少 #logPanel / #log 节点")
                    else:
                        if not after["panelInViewport"]:
                            fail("窄屏点 FAB 后日志栏不可见（宽 {}px, 右边界 {}, 视口 {}）".format(
                                after["panelW"], after["panelRight"], after["vw"]))
                        if page.evaluate(
                                "()=>document.querySelectorAll('#logPanel.open').length") == 0:
                            fail("窄屏点 FAB 后未进入 .open 抽屉态（仍在屏幕外）")
                        if after["visibleLines"] == 0:
                            fail("窄屏展开后一条日志都没渲染出来")
                        # 注意：这里不断言 atBottom。抽屉刚从屏幕外归位时，
                        # scrollHeight 在归位那一帧才确定，定位由
                        # positionLogDrawerAtLatest 的 next-frame 补刀完成；
                        # 归属断言放到 position 相关用例，避免时序假阴性。
                page.close()
            browser.close()
    finally:
        if httpd is not None:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report}, ensure_ascii=False, indent=2))
    else:
        for m in report:
            print("{:<28} 栏宽 {:<4} 日志区高 {:<5} 行数 {:<3} 视口内 {:<3} 折行 {:<3} "
                  "详情 {}/{}折叠 最新 {}".format(
                m["label"], m["panelW"], m["boxH"], m["lineCount"],
                m["inViewLines"], m["wrappedCount"],
                m.get("detailCollapsed", 0), m.get("detailCount", 0),
                "是" if m["atBottom"] else "否"))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p_ in problems:
                print("  - " + p_)
            return 1
        print("侧边栏日志面板 DOM 度量走查通过（{} 组视口，0 处问题）".format(len(report)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
