#!/usr/bin/env python3
"""媒体库显示密度的 DOM 走查（issue #53 追问）。

用户诉求原文：「新增切换显示密度的功能当前只能列表显示，实在是信息密度太低了」。

这条诉求只能在**渲染后**判定：
  - 静态扫描能证明「代码里有 lib-grid-compact」，证明不了 1440 宽下真的从 6 列
    变成了 10 列、卡片真的变窄了、首屏真的能多看见几行；
  - 也不能只断言「函数被调用了」：密度按钮点了没反应（或只改了 aria 状态）
    正是这类开关最常见的坏法。

所以本脚本真的点按钮、真的量渲染后的网格几何，逐条断言：

  1. 默认（舒适）→ 点「紧凑密度」→ 网格列数必须**真的变多**、
     卡片宽度必须真的变窄、单位面积（每行网格）的可见条目数必须真的增加
     （这是「密度」的定义）；
  2. 再点一次回到舒适，几何必须回到原样（开关可逆，不是单向的）；
  3. 紧凑密度下**键盘选择仍生效**：Ctrl+A 全选、Shift+方向键连选、当前行虚线标记
     （紧凑视图仍是 .lib-card，不能因为加密就把这套语义丢掉）。
     注意方向键按布局各取所属的一对：本脚本走的是**卡片视图**（网格，顺序是左右），
     所以是 Shift+←/→；用 ↑↓ 会去滚页面，断言会变成假失败（issue #68 修的就是
     这条路径——媒体库的卡片视图此前根本没有左右方向键）；
  4. 紧凑密度下**修改时间仍在**（issue #27 用户明确要过），只是降了字号；
  5. 布局 × 密度**互不覆盖**：切到列表视图再切回卡片视图，密度档位必须还在
     （两维各自独立）；列表视图本身也要能带着密度偏好回来；
  6. 密度偏好写进 localStorage（刷新后仍在），旧键 'lib_view' 的取值不被改写；
  7. 多视口无横向溢出（紧凑密度下更容易挤破）。

依赖：playwright + chromium（与其它媒体库走查脚本一致）。
无依赖 / 无产物时以退出码 0 跳过。

用法：
  python3 scripts/check_library_density_dom.py            # 检查（失败非零退出）
  python3 scripts/check_library_density_dom.py --json     # 机器可读报告
  python3 scripts/check_library_density_dom.py --serve    # 自动起本地静态服务
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
PORT = 8146
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# 目录条目：24 条（够撑出 3~4 行网格，列数差异才看得出来）
ENTRIES = [
    {"fid": "d%d" % i, "name": "目录-%02d" % i, "dir": True, "size": 0,
     "updated_at": 1758000000 + i}
    for i in range(1, 5)
] + [
    {"fid": "f%d" % i, "name": "文件-%02d.mkv" % i, "dir": False,
     "size": 1024 ** 3 * i, "updated_at": 1758000100 + i}
    for i in range(1, 21)
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


# 度量口径：全部基于**渲染后的**网格几何 / 勾选框 DOM，不引用任何内部变量
# —— 后者只能证明「代码以为自己切了密度」。
MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const grid = q('#libGrid');
  const cards = [...document.querySelectorAll('#libGrid .lib-card')];
  const r = el => el ? el.getBoundingClientRect() : null;
  const g = r(grid);
  // 首屏可见条目数：「一屏能看多少条」才是密度的定义，列数只是手段
  const vh = window.innerHeight;
  // 首屏可见条目：卡片落在**网格内可见区域**且未被视口下沿裁掉。
  const lim = g ? Math.min(g.top + g.height, vh) : vh;
  const inView = cards.filter(c => {
    const b = r(c);
    return b && b.top >= (g ? g.top - 4 : 0) && b.top < lim;
  }).length;
  const boxes = cards.map(c => c.querySelector('input[type=checkbox]'));
  const cur = cards.filter(c => c.classList.contains('lib-current'));
  const times = [...document.querySelectorAll('#libGrid .lib-card-time')];
  const visTime = e => [...e.querySelectorAll('.lib-time-text')]
    .filter(x => getComputedStyle(x).display !== 'none');
  return {
    isCardView: !!grid,
    compactClass: !!grid && grid.classList.contains('lib-grid-compact'),
    cols: grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0,
    cardW: cards.length ? Math.round(r(cards[0]).width) : 0,
    cardH: cards.length ? Math.round(r(cards[0]).height) : 0,
    cardCount: cards.length,
    inView: inView,
    rowsPerView: (function(){
      // 首屏能看到的**行数**（按卡片 top 去重）：密度最直观的口径
      const byTop = {};
      cards.forEach(c => {
        const b = r(c); if(!b) return;
        if(b.top < g.top - 4 || b.top > g.top + Math.min(vh, g.height)) return;
        byTop[Math.round(b.top / 5)] = 1;
      });
      return Object.keys(byTop).length;
    })(),
    // 密度真正的目标口径：**单位网格面积里的条目数**。
    // 不直接拿「首屏可见条目数」当断言，是因为它会被无关因素吃掉：
    //   * 网格 max-height 把内容裁掉时，可见条目数反而下降；
    //   * 目录条目刚好排满（24 条）时，舒适/紧凑都 24 条全可见，
    //     分母相同 → 断言恒等（本轮实测在 1440x900 上就是这样）。
    // 用「每 100px 网格高度的条目数」把这两个干扰都去掉：
    // 同一批条目，密度越高，单位高度装下的就越多。
    densityPer100: g && g.height ? Math.round(inView / g.height * 100 * 10) / 10 : 0,
    perView: inView,
    gridMaxH: grid ? Math.round(parseFloat(getComputedStyle(grid).maxHeight) || 0) : 0,
    checked: boxes.filter(b => b && b.checked).length,
    curCount: cur.length,
    curBorderStyle: cur.length ? getComputedStyle(cur[0]).borderTopStyle : '',
    dockHidden: !!q('#libDensityBtn') && false,
    densityBtnText: q('#libDensityBtn') ? q('#libDensityBtn').textContent : '',
    densityBtnPressed: q('#libDensityBtn')
      ? q('#libDensityBtn').getAttribute('aria-pressed') : null,
    viewBtnText: q('#libViewBtn') ? q('#libViewBtn').textContent : '',
    countText: q('#libSelCount') ? q('#libSelCount').textContent : '',
    timeRows: times.length,
    // 时间行在紧凑密度下必须仍然可见且完整（只允许降字号，不允许淡出/截断）
    timeVisible: times.filter(e => {
      const b = r(e);
      return b && b.height > 0 && getComputedStyle(e).display !== 'none';
    }).length,
    timeTruncated: times.filter(e => visTime(e).some(
      x => x.scrollWidth > Math.ceil(x.getBoundingClientRect().width) + 1)).length,
    timeFontPx: times.length
      ? Math.round(parseFloat(getComputedStyle(visTime(times[0])[0] || times[0]).fontSize) || 0)
      : 0,
    iconVisible: [...document.querySelectorAll('#libGrid .lib-card-icon')]
      .filter(e => getComputedStyle(e).display !== 'none').length,
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    pageOverflowX: (function(){
      const p = q('#tabLibrary');
      return p ? p.scrollWidth - p.clientWidth : 0;
    })(),
    lsDensity: (function(){ try{ return localStorage.getItem('lib_density'); }catch(e){ return null; } })(),
    lsView: (function(){ try{ return localStorage.getItem('lib_view'); }catch(e){ return null; } })(),
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

            for width, height, label in ((1440, 900, "1440x900"),
                                         (390, 844, "390x844 移动端")):
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", lambda r: _route(r))
                page.goto(URL, wait_until="load", timeout=20000)
                page.evaluate("()=>switchTab('library')")
                page.wait_for_timeout(1200)

                def st():
                    return page.evaluate(MEASURE)

                def fail(msg):
                    problems.append("[{}] {}".format(label, msg))

                # 0. 密度按钮必须存在且文案反映**当前**档位（不是悬空的按钮）
                s0 = st()
                if not s0["isCardView"]:
                    fail("默认未落在卡片视图，后面的密度断言不可信")
                    page.close()
                    continue
                if s0["cardCount"] < 12:
                    fail("目录行数异常（{} 张卡），后面的断言不可信".format(s0["cardCount"]))
                    page.close()
                    continue
                if s0["compactClass"]:
                    fail("默认就是紧凑密度（应为舒适，用户第一次进来不该被压到最密）")
                if "紧凑" not in s0["densityBtnText"]:
                    fail("舒适档下按钮文案应提示「可切到紧凑」，实际 {!r}".format(
                        s0["densityBtnText"]))
                if s0["densityBtnPressed"] != "false":
                    fail("舒适档下 aria-pressed 应为 false（{!r}）".format(
                        s0["densityBtnPressed"]))
                base = dict(s0)

                # 1. 「信息密度太低」的直接度量：点一下，列数/可见行数必须真的涨
                page.click("#libDensityBtn")
                page.wait_for_timeout(400)
                s1 = st()
                if not s1["compactClass"]:
                    fail("点「紧凑密度」后网格没有紧凑类名（开关没生效）")
                if s1["cardW"] >= base["cardW"]:
                    fail("紧凑密度下卡片宽度没有变窄（{}px -> {}px）："
                         "这与「密度高一点」是相反的".format(base["cardW"], s1["cardW"]))
                if s1["cols"] <= base["cols"]:
                    fail("紧凑密度下网格列数没有增加（{} -> {}）："
                         "一屏还是只能看这么多".format(base["cols"], s1["cols"]))
                if s1["densityPer100"] <= base["densityPer100"]:
                    fail("紧凑密度下单位面积的条目数没有增加（每 100px 网格高度 "
                         "{} -> {} 条；可见 {} -> {} 条）："
                         "「信息密度太低」没被解决".format(
                             base["densityPer100"], s1["densityPer100"],
                             base["perView"], s1["perView"]))
                if s1["cardCount"] != base["cardCount"]:
                    fail("切换密度后条目数变了（{} -> {}）：切换必须是纯展示行为".format(
                        base["cardCount"], s1["cardCount"]))
                if "舒适" not in s1["densityBtnText"] or s1["densityBtnPressed"] != "true":
                    fail("紧凑档下按钮文案/状态未反映当前档位（{!r} / {!r}）".format(
                        s1["densityBtnText"], s1["densityBtnPressed"]))
                report.append({"label": label + " 密度", "cols": [base["cols"], s1["cols"]],
                               "cardW": [base["cardW"], s1["cardW"]],
                               "perView": [base["perView"], s1["perView"]],
                               "per100": [base["densityPer100"], s1["densityPer100"]],
                               "rows": [base["rowsPerView"], s1["rowsPerView"]]})

                # 2. 修改时间不能因为加密而消失（只允许降字号）
                if s1["timeRows"] == 0 or s1["timeVisible"] != s1["timeRows"]:
                    fail("紧凑密度下修改时间行丢失（可见 {} / 共 {}）".format(
                        s1["timeVisible"], s1["timeRows"]))
                if s1["timeTruncated"] > 0:
                    fail("紧凑密度下 {} 条修改时间被截断".format(s1["timeTruncated"]))
                if s1["timeFontPx"] > base["timeFontPx"]:
                    fail("紧凑密度下时间字号反而变大了（{} -> {}）".format(
                        base["timeFontPx"], s1["timeFontPx"]))
                # 图标在紧凑卡里让位（这是有意的取舍：让出宽度给标题）。
                # 只允许「舒适可见 → 紧凑隐藏」这一个方向，且必须是真隐藏
                # （紧凑下仍显示图标 = 标题被挤窄，密度买不到宽度）。
                if s1["iconVisible"] > base["iconVisible"]:
                    fail("紧凑密度下图标可见数反而变多（{} -> {}）".format(
                        base["iconVisible"], s1["iconVisible"]))
                if base["iconVisible"] > 0 and s1["iconVisible"] != 0:
                    fail("紧凑密度未隐藏类型图标：{}px 的卡里图标会挤掉标题宽度".format(
                        s1["cardW"]))

                # 3. 紧凑密度下键盘选择仍生效（这是最容易分叉丢的一块）
                page.keyboard.press("Control+a")
                page.wait_for_timeout(200)
                sk = st()
                if sk["checked"] != sk["cardCount"]:
                    fail("紧凑密度下 Ctrl+A 未全选（勾选框 {} / 卡片 {}）".format(
                        sk["checked"], sk["cardCount"]))
                page.keyboard.press("Escape")
                page.wait_for_timeout(150)
                # 本段跑在卡片视图（网格）：方向键是 ←→，↑↓ 让位给浏览器滚动。
                page.keyboard.press("ArrowRight")
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(150)
                sd = st()
                if sd["curCount"] != 1 or sd["curBorderStyle"] != "dashed":
                    fail("紧凑密度下「当前行」标记丢失（cur={} border={!r}）".format(
                        sd["curCount"], sd["curBorderStyle"]))
                page.keyboard.press("Shift+ArrowRight")
                page.wait_for_timeout(150)
                if st()["checked"] != 2:
                    fail("紧凑密度下 Shift+方向键连选失效（checked={}）".format(st()["checked"]))
                page.keyboard.press("Escape")
                page.wait_for_timeout(120)

                # 4. 两维互不覆盖：切布局来回一趟，密度档位必须还在
                page.click("#libViewBtn")
                page.wait_for_timeout(400)
                sl = st()
                if sl["isCardView"]:
                    fail("切到列表视图失败（#libGrid 仍在）")
                if sl["docOverflowX"] > 0 or sl["pageOverflowX"] > 0:
                    fail("列表视图横向溢出（doc {} / page {}）".format(
                        sl["docOverflowX"], sl["pageOverflowX"]))
                page.click("#libViewBtn")
                page.wait_for_timeout(400)
                sb = st()
                if not sb["isCardView"]:
                    fail("切回卡片视图失败")
                if not sb["compactClass"]:
                    fail("切布局往返之后密度档位被重置了（应保持紧凑）")
                if sb["cols"] != s1["cols"]:
                    fail("切布局往返后网格列数变了（{} -> {}）".format(s1["cols"], sb["cols"]))
                if sb["lsDensity"] != "compact":
                    fail("密度偏好未持久化（localStorage lib_density={!r}）".format(
                        sb["lsDensity"]))
                if sb["lsView"] != "card":
                    fail("布局偏好被密度切换带歪（lib_view={!r}）".format(sb["lsView"]))

                # 5. 溢出（紧凑密度列更多，最容易挤破）
                if s1["docOverflowX"] > 0 or s1["pageOverflowX"] > 0:
                    fail("紧凑密度下横向溢出（doc {} / page {}）".format(
                        s1["docOverflowX"], s1["pageOverflowX"]))
                if s1["cardW"] < 110:
                    fail("紧凑密度下卡片宽度 {}px 过窄（标题会被压成一个字）".format(
                        s1["cardW"]))

                # 6. 可逆：再点一次必须回到舒适档的几何
                page.click("#libDensityBtn")
                page.wait_for_timeout(400)
                s2 = st()
                if s2["compactClass"]:
                    fail("再次点击密度按钮没有回到舒适档（开关不是可逆的）")
                if s2["cols"] != base["cols"] or s2["cardW"] != base["cardW"]:
                    fail("切回舒适档后几何未复原（cols {} -> {} / cardW {} -> {}）".format(
                        base["cols"], s2["cols"], base["cardW"], s2["cardW"]))
                if s2["lsDensity"] != "comfortable":
                    fail("回到舒适档未持久化（lib_density={!r}）".format(s2["lsDensity"]))

                # 7. 刷新后密度仍在（localStorage 真的被读了，不只是写了）
                page.click("#libDensityBtn")
                page.wait_for_timeout(300)
                page.reload(wait_until="load", timeout=20000)
                page.evaluate("()=>switchTab('library')")
                page.wait_for_timeout(1200)
                sr = st()
                if not sr["compactClass"]:
                    fail("刷新后密度偏好丢失（写进了 localStorage 但没被读回来）")
                report.append({"label": label + " 收尾", "cols": sr["cols"],
                               "cardW": sr["cardW"], "ls": sr["lsDensity"]})
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
            print("{:<22} {}".format(m.pop("label"), json.dumps(m, ensure_ascii=False)))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for x in problems:
                print("  - " + x)
            return 1
        print("媒体库显示密度走查通过（0 处问题）")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
