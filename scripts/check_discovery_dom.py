#!/usr/bin/env python3
"""发现区多选操作条（#tmdbSelBar）DOM 走查（issue #15 第 3 轮）。

用途：
  在真实浏览器里加载构建产物、注入确定性的 TMDB 桩数据，点选一张海报卡，
  对「选中之后能不能点到转存按钮」做可量化断言。这些指标静态扫描表达不了
  （tests/test_build.py 只能查「有没有写 filter:none」，查不出层叠是否真的生效）。

  issue #15 第 3 轮的原表现：选中资源后**看不到转存按钮**。
  真凶是 CSS 包含块：`position:fixed` 的元素只要祖先里有
  `filter` / `backdrop-filter` / `transform` / `perspective` / `contain:paint`，
  就不再相对视口定位，而是相对那个祖先定位。发现区的卡片与面板都带
  `backdrop-filter: var(--blur)`，于是选中栏被钉在分区内部 ——
  实测 1440×900 下 top=1361（视口高只有 900），按钮整体落在屏幕下方。

  本脚本断言（9 组视口，逐条）：
  1. 点选后 #tmdbSelBar 必须进入 .active 且完全不透明（opacity ≥ 0.9）；
  2. 它的上/下/左/右四条边都不得越出视口；
  3. 「开始转存」按钮中心的命中测试必须落回 #tmdbSelBar 自身
     （挡住它的悬浮按钮 / 侧栏 / 日志栏都会让命中变成别的元素）；
  4. 浮层不得换行变高（> 66px 说明按钮组被挤到第二行）。

依赖：playwright + chromium（与 scripts/check_overview_dom.py 同依赖），
  pip install playwright && python -m playwright install chromium
无依赖时脚本以退出码 0 跳过（避免影响不装浏览器的 CI 阶段）。

用法：
  python3 scripts/check_discovery_dom.py            # 检查（失败非零退出）
  python3 scripts/check_discovery_dom.py --json     # 机器可读报告
  python3 scripts/check_discovery_dom.py --serve    # 自动起本地静态服务
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
PORT = 8126
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# ---------------------------------------------------------------- 桩数据
# 12 部影片足够铺满一屏海报墙（视口最窄一档是 390px，2 列 × 3 行可见），
# 保证「点选第一张卡」在任何视口下都是真实可见的卡片。
_ITEMS = [
    {"id": 100 + i, "title": "影片 %d" % i, "poster": "", "rating": 7.5,
     "year": 2024, "votes": 1200, "overview": ""}
    for i in range(12)
]

STUBS = {
    "/api/tmdb/options": {
        "regions": [{"code": "US", "name": "美国"}],
        "languages": [{"code": "zh-CN", "name": "中文"}],
        "movie_list_types": [{"code": "trending", "name": "热映"}],
        "tv_list_types": [{"code": "trending", "name": "热映"}],
    },
    "/api/tmdb/list": {"items": _ITEMS, "total_pages": 1, "page": 1},
    "/api/tmdb/genres": {"genres": [{"id": 28, "name": "动作"}]},
    "/api/config": {"tmdb_api_key": "***"},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.1.0"},
    "/api/history": {"total": 0, "items": {}},
}

# 浮层透明度下限：active 态必须是完全不透明（CSS 的 transition 需要时间，测量前已等待）
MIN_OPACITY = 0.9
# 浮层高度上限：单行约 54~58px；超过 66px 说明按钮组换行（会自己盖住自己）
MAX_BAR_H = 66

MEASURE = r"""
() => {
  const bar = document.querySelector('#tmdbSelBar');
  if (!bar) return {missing: 'no #tmdbSelBar'};
  const btn = document.querySelector('#tmdbTransferBtn');
  if (!btn) return {missing: 'no #tmdbTransferBtn'};
  const r = bar.getBoundingClientRect();
  const br = btn.getBoundingClientRect();
  const cx = Math.round(br.left + br.width / 2);
  const cy = Math.round(br.top + br.height / 2);
  const hit = document.elementFromPoint(cx, cy);
  const cs = getComputedStyle(bar);
  // 定位祖先探针：列出把自己变成 fixed 包含块的祖先（根因诊断，便于失败时定位）
  const traps = [];
  let e = bar.parentElement;
  while (e) {
    const s = getComputedStyle(e);
    const bad = [];
    if (s.filter !== 'none') bad.push('filter');
    if ((s.backdropFilter || s.webkitBackdropFilter || 'none') !== 'none') bad.push('backdrop-filter');
    if (s.transform !== 'none') bad.push('transform');
    if (s.perspective !== 'none') bad.push('perspective');
    if (s.contain && /paint|layout|strict|content/.test(s.contain)) bad.push('contain:' + s.contain);
    if (bad.length) traps.push((e.id ? '#' + e.id : e.tagName.toLowerCase()) + ' { ' + bad.join(', ') + ' }');
    e = e.parentElement;
  }
  return {
    active: bar.classList.contains('active'),
    opacity: parseFloat(cs.opacity),
    transform: cs.transform,
    bar: {t: Math.round(r.top), b: Math.round(r.bottom),
          l: Math.round(r.left), r: Math.round(r.right), h: Math.round(r.height)},
    btn: {t: Math.round(br.top), l: Math.round(br.left),
          w: Math.round(br.width), h: Math.round(br.height)},
    viewport: {w: window.innerWidth, h: window.innerHeight},
    hit: hit ? (hit.id || (typeof hit.className === 'string' ? hit.className : hit.tagName)) : null,
    hitIsBar: !!(hit && hit.closest('#tmdbSelBar')),
    traps: traps,
  };
}
"""

# (宽度, 高度, 标签) —— 覆盖 issue #15 涉及的桌面 / 中间宽度 / 平板 / 移动
VIEWPORTS = [
    (2560, 1392, "2560x1392 超宽屏"),
    (1920, 1080, "1920x1080 大屏"),
    (1600, 900, "1600x900"),
    (1440, 900, "1440x900"),
    (1280, 900, "1280x900"),
    (1200, 900, "1200x900 日志栏转覆盖层"),
    (1024, 768, "1024x768 侧栏收起"),
    (760, 900, "760x900 两列堆叠"),
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
            browser = p.chromium.launch(args=["--no-sandbox"])
            for width, height, label in VIEWPORTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", handler)
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(1100)
                # 滚到发现区（概览页首屏很长，不滚过去点不到卡片）
                page.evaluate("()=>{ try{ jumpToDiscovery(); }catch(e){} }")
                page.wait_for_timeout(1000)

                cards = page.query_selector_all("#tmdbGrid .tmdb-card")
                m = {"label": label, "width": width, "height": height}
                if not cards:
                    problems.append("[{}] 发现区没有渲染出海报卡片".format(label))
                    report.append(m)
                    page.close()
                    continue

                cards[0].click()
                # 等浮层 transition（.35s）结束再测量，避免把动画中间态判成缺陷
                page.wait_for_timeout(700)
                m.update(page.evaluate(MEASURE))
                report.append(m)

                def fail(msg):
                    problems.append("[{}] {}".format(label, msg))

                if m.get("missing"):
                    fail(m["missing"])
                    page.close()
                    continue

                if not m["active"]:
                    fail("点选卡片后 #tmdbSelBar 未进入 .active")
                if m["opacity"] < MIN_OPACITY:
                    fail("点选后转存栏仍不可见 opacity={}（下限 {}）".format(
                        m["opacity"], MIN_OPACITY))
                b, vp = m["bar"], m["viewport"]
                if b["t"] < 0 or b["b"] > vp["h"] + 1:
                    fail("转存栏纵向越界 top={} bottom={}（视口高 {}）① ".format(
                        b["t"], b["b"], vp["h"]))
                if b["l"] < -1 or b["r"] > vp["w"] + 1:
                    fail("转存栏横向越界 left={} right={}（视口宽 {}）".format(
                        b["l"], b["r"], vp["w"]))
                if b["h"] > MAX_BAR_H:
                    fail("转存栏换行变高 {}px（上限 {}px，按钮组被挤到第二行）".format(
                        b["h"], MAX_BAR_H))
                if not m["hitIsBar"]:
                    fail("「开始转存」中心命中到「{}」而不是转存栏（被浮层遮挡）".format(m["hit"]))
                    if m["traps"]:
                        fail("  定位包含块探针：{}".format("; ".join(m["traps"])))
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
            if m.get("missing") or "bar" not in m:
                print("{:<26} 无法测量（{}）".format(m["label"], m.get("missing", "-")))
                continue
            print("{:<26} 转存栏 top={:<5} h={:<3} 可点击={}".format(
                m["label"], m["bar"]["t"], m["bar"]["h"], m["hitIsBar"]))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p_ in problems:
                print("  - " + p_)
            return 1
        print("发现区多选操作条 DOM 走查通过（{} 组视口，0 处问题）".format(len(report)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
