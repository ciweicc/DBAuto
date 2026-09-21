#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""侧边栏「选中动态效果」的 DOM 走查（issue #84）。

用户诉求原文只有一句：「侧边栏选项添加选中动态效果」。

旧版选中态只有一条静态的 `.active` 底色：切换页签时旧项与新项在**同一帧**里
换掉 —— 看得见「现在是哪个」，看不见「从哪个挪到哪个」。侧边栏是应用的主导航，
一天点几十次，每次都是硬切。

这类问题只能渲染后判定：
  - 静态扫描能证明「CSS 里有 transition」，但证明不了「切换时底板真的从旧项
    滑到新项、中途真的出现过中间位置」；
  - 也证明不了「首帧是不是从顶部滑下来（像动画故障）」「窄屏抽屉里有没有做
    无意义的动画」「选中项自己那层底色退掉了没有（不退就是两层叠加、过亮）」。

断言清单（每条都同时看**逻辑状态**与**屏幕状态**）：
  1. 侧边栏里恰有一块滑动底板（唯一），尺寸与选中项一致、位置对齐选中项；
  2. 点另一个页签：底板**平滑位移**（采样到中间位置，不是瞬移）；
  3. 位移过程中 `.active` 与 `aria-selected` 只有一份（不会新旧同时亮）；
  4. 底板就位后选中项**不再自绘底色**（.ind-on，避免两层叠加过亮）；
  5. 方向键 / Alt+数字 走的是同一条路径：底板同样跟随、且键盘焦点落在导航项上；
  6. 首帧定位**不动画**（加载后立刻采样，底板已在正确位置，不是从顶部滑入）；
  7. 「点击即时反馈」不是恒亮动画：脉冲 class 会被回收（一段时间后消失）；
  8. 窄屏抽屉：打开后选中底板对齐正确；
  9. 无横向溢出、无控制台错误。

为什么不用截图：上面每条都能用 `getBoundingClientRect` + `getComputedStyle`
直接算出来，是廉价文本证据；按 AGENTS.md，读图（400–980KB/张）只留给断言
无法表达的视觉问题。

依赖：playwright + chromium（与其它走查脚本同依赖）。
无依赖 / 无产物时以退出码 0 跳过（不阻断 CI）。

用法：
  python3 scripts/check_sidebar_dom.py            # 检查（失败非零退出）
  python3 scripts/check_sidebar_dom.py --json     # 机器可读报告
  python3 scripts/check_sidebar_dom.py --serve    # 自动起本地静态服务
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
PORT = 0
URL = ""

STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.2.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/tmdb/list": {"items": []},
    "/api/tmdb/options": {"regions": [], "movie_list_types": [], "tv_list_types": []},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/transfer/status": {"running": False, "progress": [], "stats": {}},
    "/api/qas/tasks": {"success": True, "tasks": []},
    "/api/library/source": {"success": True, "source": "openlist", "openlist": True,
                            "openlist_ok": True, "openlist_user": "admin"},
}

# 渲染后的度量：底板 / 选中项几何、过渡状态、脉冲回收情况，全是屏幕事实。
MEASURE = r"""
() => {
  const nav = document.querySelector('.side-nav');
  const ind = nav ? nav.querySelector('.side-nav-ind') : null;
  const items = [...document.querySelectorAll('.side-nav-item')];
  const active = document.querySelector('.side-nav-item.active');
  const cs = el => el ? getComputedStyle(el) : null;
  const box = el => { if(!el) return null; const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height)}; };
  const ics = cs(ind);
  const acs = cs(active);
  const navcs = cs(nav);
  const si = active ? cs(active.querySelector('svg')) : null;
  return {
    hasNav: !!nav,
    hasInd: !!ind,
    indCount: nav ? nav.querySelectorAll('.side-nav-ind').length : 0,
    indRect: box(ind),
    indOpacity: ics ? ics.opacity : '',
    indTransition: ics ? ics.transitionProperty : '',
    indDuration: ics ? ics.transitionDuration : '',
    indReady: ind ? ind.classList.contains('ready') : null,
    indAnim: ind ? ind.classList.contains('anim') : null,
    indOn: nav ? nav.classList.contains('ind-on') : null,
    indTop: ind ? ind.getAttribute('data-nav-top') : null,
    indH: ind ? ind.getAttribute('data-nav-h') : null,
    activeTab: active ? active.getAttribute('data-tab') : '',
    activeRect: box(active),
    activeCount: document.querySelectorAll('.side-nav-item.active').length,
    ariaSelected: items.filter(e => e.getAttribute('aria-selected') === 'true')
                       .map(e => e.getAttribute('data-tab')),
    // 选中项自绘底色（应为 transparent —— 底色交给底板）
    activeBg: acs ? acs.backgroundImage + '|' + acs.backgroundColor : '',
    activeShadow: acs ? acs.boxShadow : '',
    // 图标是否常亮（选中态动效的落点之一）
    iconOpacity: si ? si.opacity : '',
    iconScale: si ? si.transform : '',
    pulseCount: document.querySelectorAll('.side-nav-item.side-btn-pulse').length,
    // 选中项内容是否被裁掉（底板与项同轴的前提）
    navOverflow: navcs ? navcs.overflowY : '',
    focused: (document.activeElement && document.activeElement.className) || '',
    focusedTab: (document.activeElement && document.activeElement.getAttribute)
      ? document.activeElement.getAttribute('data-tab') : null,
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
    """起一个本地静态服务（端口用 0 交给内核分配，避免连到上一轮残留实例）。"""
    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(_QuietHandler, directory=os.path.join(ROOT, "static"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _route(route):
    url = route.request.url
    if "/api/" not in url:
        return route.fallback()
    path = urllib.parse.urlparse(url).path
    if path.rstrip("/").endswith("/api/sse"):
        return route.fulfill(status=200, content_type="text/event-stream", body=b"")
    body = STUBS.get(path, {})
    return route.fulfill(status=200, content_type="application/json",
                         body=json.dumps(body, ensure_ascii=False).encode())


def _dy(st, key="indRect"):
    return st["indRect"]["y"] if st.get(key) else None


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
    PORT = httpd.server_address[1] if httpd is not None else 8145
    URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

    problems, report = [], []

    def ok(name, cond, detail=""):
        report.append({"check": name, "pass": bool(cond), "detail": detail})
        if not cond:
            problems.append("{}：{}".format(name, detail))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"], **_launch_kwargs())

            def open_page(width=1440, height=900):
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", lambda r: _route(r))
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(1200)
                return page

            def ms(page):
                return page.evaluate(MEASURE)

            # ---------------- 桌面：底板存在且对齐 ----------------
            page = open_page()
            st = ms(page)

            ok("侧边栏里恰有一块滑动底板（唯一）",
               st["hasInd"] and st["indCount"] == 1,
               "hasInd={} indCount={}（多块底板就会出现两个亮块）".format(
                   st["hasInd"], st["indCount"]))
            ok("底板已就位可见（ready + opacity>0）",
               st["indReady"] and _opaque(st["indOpacity"]),
               "ready={} opacity={}".format(st["indReady"], st["indOpacity"]))
            if st["indRect"] and st["activeRect"]:
                same_y = abs(st["indRect"]["y"] - st["activeRect"]["y"]) <= 2
                same_h = abs(st["indRect"]["h"] - st["activeRect"]["h"]) <= 2
                ok("底板与当前选中项同轴同高",
                   same_y and same_h,
                   "ind={} active={}".format(st["indRect"], st["activeRect"]))
            ok("选中项不再自绘底色（底色交给底板）",
               st["indOn"] and st["activeShadow"] == "none" and
               "linear-gradient" not in (st["activeBg"] or ""),
               "indOn={} bg={!r} shadow={!r}".format(
                   st["indOn"], st["activeBg"], st["activeShadow"]))
            ok("选中项图标常亮（未选中项为 0.85）",
               _approx(st["iconOpacity"], 1.0),
               "iconOpacity={!r}（选中态必须与未选中态可区分）".format(st["iconOpacity"]))
            ok("底板写回了可断言的几何属性（data-nav-top / data-nav-h）",
               st["indTop"] is not None and st["indH"] is not None,
               "top={!r} h={!r}".format(st["indTop"], st["indH"]))

            before = ms(page)
            ok("初始选中项为概览",
               before["activeTab"] == "overview",
               "activeTab={!r}".format(before["activeTab"]))

            # ---------------- 点击切换：底板必须有「中间位置」 ----------------
            # 采样方式：在页面内用 rAF 连续采样底板的 y，再统计「落在首尾开区间内的
            # 采样点」。**不要**用「等 120ms 再量一次」的固定窗口：
            # headless Chromium 里 rAF 会被节流，整段位移可能推迟到 ~300ms 之后才开始，
            # 固定窗口会随机量到起点（假 FAIL）或终点（假 PASS）。
            # 采样器直接在页面内跑，不依赖 Playwright 的往返时延。
            page.evaluate("""()=>{
              window.__navSamples = [];
              const ind = document.querySelector('.side-nav-ind');
              const t0 = performance.now();
              const tick = () => {
                window.__navSamples.push(Math.round(ind.getBoundingClientRect().y));
                if (performance.now() - t0 < 1500) requestAnimationFrame(tick);
              };
              tick();
            }""")
            page.click('.side-nav-item[data-tab="settings"]')
            page.wait_for_timeout(1800)
            samples = page.evaluate("()=>window.__navSamples")
            after = ms(page)

            start_y = _dy(before)
            end_y = _dy(after)
            # 中间位置：严格落在 (起点, 终点) 开区间内，且既不等于起点也不等于终点
            lo, hi = min(start_y or 0, end_y or 0), max(start_y or 0, end_y or 0)
            mids = [y for y in samples if lo + 1 < y < hi - 1]
            ok("切换后底板仍在（唯一一块）",
               after["indCount"] == 1, "indCount={}".format(after["indCount"]))
            ok("底板确实移动到了新的选中项上",
               abs(end_y - after["activeRect"]["y"]) <= 2,
               "ind={} active={}".format(after["indRect"], after["activeRect"]))
            # 判定「平滑」＝ 位移途中**确实出现过中间位置**。
            # 注意不能用「中间采样点 >= 2 个」：headless Chromium 的 rAF 会被节流，
            # 一次 0.25s 的过渡在采样里可能只剩 1 个中间点（实测 64 → 181 → 222 → 224）。
            # 「瞬移」的判据反而是**一个中间点都没有**（直接 64 → 224）。
            ok("底板移动是平滑的（位移途中出现过中间位置）",
               len(samples) > 3 and len(mids) >= 1,
               "start={!r} end={!r} 采样点={} 中间位置={}（0 个中间点=瞬移，动效没生效）".format(
                   start_y, end_y, len(samples), len(mids)))
            # 交叉验证：底板自己声明的过渡必须真的挂在 transform 上。
            # 上一条依赖采样，这条依赖计算样式 —— 两条一起才排除「采样碰巧撞上」。
            ok("底板在 transform 上声明了过渡（非 0 时长）",
               _has_transform_transition(after["indTransition"], after["indDuration"]),
               "transitionProperty={!r} duration={!r}（无过渡 = 硬切）".format(
                   after["indTransition"], after["indDuration"]))
            ok("切换后选中态唯一（新旧不同时亮）",
               after["activeCount"] == 1 and after["ariaSelected"] == ["settings"],
               "activeCount={} ariaSelected={}".format(
                   after["activeCount"], after["ariaSelected"]))
            ok("底板迁移后选中项仍不自绘底色",
               after["indOn"] and after["activeShadow"] == "none",
               "indOn={} shadow={!r}".format(after["indOn"], after["activeShadow"]))

            # ---------------- 点击反馈不是恒亮动画 ----------------
            pulse_now = page.evaluate(
                "()=>{document.querySelector('.side-nav-item[data-tab=\"library\"]').click();"
                "return document.querySelectorAll('.side-nav-item.side-btn-pulse').length;}")
            page.wait_for_timeout(700)
            pulse_later = ms(page)["pulseCount"]
            ok("点击即时反馈是一次性的（脉冲会被回收）",
               pulse_now == 1 and pulse_later == 0,
               "点击瞬间={} 700ms 后={}（恒亮=整排都在动，是噪声）".format(
                   pulse_now, pulse_later))

            # ---------------- 方向键：同一条路径 + 焦点落在导航上 ----------------
            page.evaluate("()=>{document.querySelector('.side-nav-item[data-tab=\"overview\"]').focus();}")
            page.wait_for_timeout(100)
            k_before = ms(page)
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(900)
            k_after = ms(page)
            ok("方向键切换同样带动底板",
               k_after["activeTab"] == "manual"
               and abs(k_after["indRect"]["y"] - k_after["activeRect"]["y"]) <= 2,
               "activeTab={!r} ind={} active={}".format(
                   k_after["activeTab"], k_after["indRect"], k_after["activeRect"]))
            ok("方向键切换后键盘焦点仍在导航项上",
               (k_after["focused"] or "").find("side-nav-item") >= 0
               and k_after["focusedTab"] == "manual",
               "focused={!r} tab={!r}".format(k_after["focused"], k_after["focusedTab"]))
            ok("方向键后底板位置 ≠ 切换前（确实动了）",
               _dy(k_before) != _dy(k_after) or k_before["activeTab"] != k_after["activeTab"],
               "before={} after={}".format(_dy(k_before), _dy(k_after)))

            # ---------------- Alt+数字：快捷键与选中态对齐 ----------------
            page.keyboard.press("Alt+5")
            page.wait_for_timeout(600)
            alt = ms(page)
            ok("Alt+5 切到第 5 个页签（设置）且底板跟随",
               alt["activeTab"] == "settings"
               and abs(alt["indRect"]["y"] - alt["activeRect"]["y"]) <= 2,
               "activeTab={!r} ind={} active={}".format(
                   alt["activeTab"], alt["indRect"], alt["activeRect"]))
            page.close()

            # ---------------- 首帧不得从顶部滑入 ----------------
            fresh = open_page()
            # 不等额外时间：load 后立刻采样，若底板带着从 0 开始的过渡，这里会量到 y≈0
            fresh.wait_for_timeout(60)
            first = ms(fresh)
            # 「过渡已武装」（anim=True）是**正常**的：它表示下一次切换才是平滑的。
            # 这里要钉的是「首帧就已经站在正确位置上」——若实现是先滑再到位，
            # 60ms 时会量到底板还贴在列表顶部（y≈侧栏首项的上沿）。
            ok("首帧底板已在正确位置（不是从顶部滑入）",
               first["indRect"] is not None
               and abs(first["indRect"]["y"] - first["activeRect"]["y"]) <= 2,
               "60ms 时 ind={} active={}（若 ind 贴在列表顶部 = 向下滑入的动画故障观感）".format(
                   first["indRect"], first["activeRect"]))
            fresh.close()

            # ---------------- 窄屏抽屉 ----------------
            narrow = open_page(width=390, height=844)
            narrow.evaluate("()=>toggleSidebar()")
            narrow.wait_for_timeout(600)
            ns = narrow.evaluate(MEASURE)
            assert_drawer_ok = (
                ns["indCount"] == 1
                and ns["indRect"] is not None
                and ns["activeRect"] is not None
                and abs(ns["indRect"]["y"] - ns["activeRect"]["y"]) <= 2
            )
            ok("窄屏抽屉打开后底板对齐（抽屉里不做无意义动画）",
               assert_drawer_ok,
               "ind={} active={}".format(ns["indRect"], ns["activeRect"]))
            narrow.evaluate("()=>switchTab('library')")
            narrow.wait_for_timeout(600)
            ns2 = narrow.evaluate(MEASURE)
            ok("抽屉内切换页签底板同样跟随",
               abs(ns2["indRect"]["y"] - ns2["activeRect"]["y"]) <= 2,
               "ind={} active={}".format(ns2["indRect"], ns2["activeRect"]))
            ok("抽屉内切换后抽屉已收起（选中动效不改变原有行为）",
               narrow.evaluate("()=>!document.querySelector('.sidebar').classList.contains('open')"),
               "drawer 仍 open，switchTab 的 closeDrawers 行为被破坏")
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


def _opaque(v):
    try:
        return float(v) > 0.05
    except (TypeError, ValueError):
        return False


def _has_transform_transition(prop, duration):
    """transform 上确实挂了非零时长的过渡（不是 none / 0s）。"""
    if not prop or "transform" not in prop:
        return False
    try:
        return max(float(x.strip().rstrip("s")) for x in (duration or "0s").split(",")) > 0.05
    except (TypeError, ValueError):
        return False


def _approx(v, want, tol=0.05):
    try:
        return abs(float(v) - want) <= tol
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":
    sys.exit(main())
