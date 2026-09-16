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
  4. 浮层不得换行变高（> 66px 说明按钮组被挤到第二行）；
  5. issue #15 第 5 轮（筛选栏「太素」）追加：筛选控件必须自带视觉层次 ——
     底卡色阶与面板底色不同 / 圆角 ≥ 8px / 字段标签带图标 / 高级筛选整块有内层底卡 /
     类型 chips 为胶囊且选中态有强调色——并且这些都不能靠「把面板压暗」实现
     （面板底色必须保持 > 50% 透光，禁止用大块不透明底遮住模糊感）。

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
import re
import sys
import threading
import time

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
        # 与后端 MOVIE_LIST_TYPES 对齐（含 discover：高级筛选的可用态由它驱动）
        "movie_list_types": [{"code": "trending", "name": "热门趋势"},
                             {"code": "top_rated", "name": "高分"},
                             {"code": "discover", "name": "自定义筛选"}],
        "tv_list_types": [{"code": "trending", "name": "热门趋势"},
                          {"code": "discover", "name": "自定义筛选"}],
    },
    "/api/tmdb/list": {"items": _ITEMS, "total_pages": 1, "page": 1},
    "/api/tmdb/genres": {"genres": [{"id": 28, "name": "动作"}, {"id": 35, "name": "喜剧"},
                                    {"id": 18, "name": "剧情"}]},
    "/api/config": {"tmdb_api_key": "***"},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.1.0"},
    "/api/history": {"total": 0, "items": {}},
}

# 浮层透明度下限：active 态必须是完全不透明（CSS 的 transition 需要时间，测量前已等待）
MIN_OPACITY = 0.9
# 浮层高度上限：单行约 54~58px；超过 66px 说明按钮组换行（会自己盖住自己）
MAX_BAR_H = 66

# ---------------- 筛选栏视觉层次门禁（issue #15 第 5 轮）----------------
# 为什么用这些阈值：这轮改动的诉求是「筛选框太素」，判定标准必须能被机器复核。
#   · 层次 = 控件底色与它所在面板底色**必须不同**（同色叠同色正是「素」的根因）
#   · 圆角 ≥ 8px：旧版 4px 与面板 10px 不协调，控件看起来像表格格子
#   · 每个筛选字段都要有图标（纯文字标签是「素」的另一半）
#   · 类型 chips 选中态必须用强调色（旧版是绿色，与页面主色不是一套）
MIN_SELECT_RADIUS = 8
MIN_SELECT_CONTRAST = 4          # 控件底色与面板底色的 RGB 通道平均差
MAX_PANEL_ALPHA = 0.6            # 面板底色 alpha 上限（保持磨砂，禁用实底遮罩）
# 面板底色的兜底解析值（rgba(22,23,28,.55)）——浏览器把 alpha 合成后的兜底口径
PANEL_BG_REF = "rgba(22, 23, 28, 0.55)"

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
    filters: _filtersProbe(),
  };
}

// ---------- 筛选栏视觉层次探针（issue #15 第 5 轮）----------
// 只读计算样式 + 几何，不截图：视觉层次里「能写成数字」的部分全部在这里判。
function _filtersProbe(){
  const q = s => document.querySelector(s);
  const qa = s => [...document.querySelectorAll(s)];
  // "rgb(r, g, b)" / "rgba(r, g, b, a)" → {r,g,b,a}
  const parse = c => {
    const m = String(c || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x.trim()));
    return {r: p[0] || 0, g: p[1] || 0, b: p[2] || 0, a: p.length > 3 ? p[3] : 1};
  };
  const diff = (a, b) => a && b ? Math.round((Math.abs(a.r-b.r) + Math.abs(a.g-b.g) + Math.abs(a.b-b.b)) / 3) : null;
  const panel = q('.ov-panel-discovery');
  const panelBg = panel ? parse(getComputedStyle(panel).backgroundColor) : null;
  const selects = qa('.tmdb-filters .tmdb-select');
  const groups = qa('.tmdb-filters .tmdb-filter-group');
  const labels = qa('.tmdb-filters .tmdb-filter-group > label');
  const chips = qa('#tmdbGenreChips .chip');
  const adv = q('#tmdbAdv');
  const chipOn = chips.find(c => c.classList.contains('on')) || null;
  const radius = el => el ? parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0 : 0;
  return {
    selectCount: selects.length,
    // 控件底色 vs 面板底色：差值太小 = 控件「融进」面板 = 素
    selectBg: selects.length ? getComputedStyle(selects[0]).backgroundColor : null,
    selectAlpha: selects.length ? parse(getComputedStyle(selects[0]).backgroundColor).a : null,
    panelBg: panel ? getComputedStyle(panel).backgroundColor : null,
    panelAlpha: panelBg ? panelBg.a : 1,
    // 半透明控件底色要和面板底色合成后再比（否则比的是两种不透明度，口径不一致）
    bgDiff: (() => {
      if (!selects.length) return null;
      const sc = parse(getComputedStyle(selects[0]).backgroundColor);
      const pc = parse(getComputedStyle(panel).backgroundColor);
      if (!sc || !pc) return null;
      const mix = (c, bg) => Math.round(c.a * 255 + (1 - c.a) * bg);   // 深色主题底层近似为暗色
      const flat = {r: sc.a < 1 ? mix(sc, 10) : sc.r,
                    g: sc.a < 1 ? mix(sc, 10) : sc.g,
                    b: sc.a < 1 ? mix(sc, 10) : sc.b};
      return diff(flat, {r: pc.a < 1 ? 12 : pc.r, g: pc.a < 1 ? 12 : pc.g, b: pc.a < 1 ? 14 : pc.b});
    })(),
    selectRadius: radius(selects[0]),
    // 字段标签图标：每个 .tmdb-filter-group 都必须有 .tmdb-ficon
    groupCount: groups.length,
    groupIcons: groups.filter(g => g.querySelector('svg.tmdb-ficon')).length,
    labelTexts: labels.map(l => (l.textContent || '').trim()),
    // 高级筛选：整块必须有独立底卡（与面板底色不同）且带圆角
    advBg: adv ? getComputedStyle(adv).backgroundColor : null,
    advRadius: radius(adv),
    advBgDiff: adv ? diff(parse(getComputedStyle(adv).backgroundColor), panelBg) : null,
    // 类型 chips：胶囊 + 选中态强调色
    chipRadius: radius(chipOn),
    chipHeights: chips.map(c => Math.round(c.getBoundingClientRect().height)),
    chipOnColor: chipOn ? getComputedStyle(chipOn).color : null,
    chipOnBg: chipOn ? getComputedStyle(chipOn).backgroundColor : null,
    // 重置按钮必须存在且可点
    resetBtn: (() => {
      const b = q('#tmdbResetBtn');
      if (!b) return null;
      const r = b.getBoundingClientRect();
      return {w: Math.round(r.width), h: Math.round(r.height), text: (b.textContent || '').trim()};
    })(),
    // 标题：模块名必须叫「TMDB 发现」
    panelTitle: (() => { const t = q('#tmdbPanelTitle'); return t ? (t.textContent || '').trim() : null; })(),
    // 分组标题栏（快捷筛选 / 高级筛选）
    capTitles: qa('#tmdbFilters .tmdb-fcap-title, #tmdbFilters .tmdb-adv .tmdb-fcap-title').map(e => e.textContent.trim()),
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
                # 等浮层 transition 结束再测量，避免把动画中间态判成缺陷。
                # 用「轮询直到 opacity 达到下限」而不是固定 sleep：浮层的
                # transform .35s + opacity .3s 在超宽视口/机器忙时可能超出 700ms，
                # 固定等待会把「还在动画中」误判成「浮层不可见」，
                # 实测在 2560×1392 上偶发（同样的产物时通过时失败）。
                deadline = time.time() + 4.0
                while time.time() < deadline:
                    probe = page.evaluate(
                        "() => parseFloat(getComputedStyle(document.querySelector('#tmdbSelBar')).opacity)")
                    if probe >= MIN_OPACITY:
                        break
                    page.wait_for_timeout(100)
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

                # ---------- 筛选栏视觉层次（issue #15 第 5 轮）----------
                f = m.get("filters") or {}

                def ffail(msg):
                    fail("[筛选栏] " + msg)

                if f.get("panelTitle") != "TMDB 发现":
                    ffail("模块标题应为「TMDB 发现」，实测「{}」".format(f.get("panelTitle")))
                if not f.get("selectCount"):
                    ffail("没有量到任何筛选控件（.tmdb-filters .tmdb-select）")
                else:
                    if f.get("selectRadius", 0) < MIN_SELECT_RADIUS:
                        ffail("筛选控件圆角只有 {}px（下限 {}px，旧版 4px 观感像表格格子）".format(
                            f.get("selectRadius"), MIN_SELECT_RADIUS))
                    if f.get("bgDiff") is None or f["bgDiff"] < MIN_SELECT_CONTRAST:
                        ffail("筛选控件与面板底色几乎同色（通道均差 {}，下限 {}）—— "
                              "控件融进面板就是「太素」的根因".format(f.get("bgDiff"), MIN_SELECT_CONTRAST))
                if f.get("groupCount") and f.get("groupIcons") != f.get("groupCount"):
                    ffail("有筛选字段没有图标：{}/{} 个字段带图标".format(
                        f.get("groupIcons"), f.get("groupCount")))
                if f.get("advBgDiff") is None or f["advBgDiff"] < 3:
                    ffail("高级筛选没有独立底卡（与面板底色通道均差 {}，下限 3）".format(
                        f.get("advBgDiff")))
                if f.get("chipRadius", 0) < 100:
                    ffail("类型 chips 未胶囊化（圆角 {}px）".format(f.get("chipRadius")))
                if f.get("chipOnColor"):
                    # 选中态必须落在强调色系（蓝）：蓝色通道须明显高于红色通道
                    rgb = [int(x) for x in re.findall(r"\d+", f["chipOnColor"])[:3]] or [0, 0, 0]
                    if not (rgb[2] > rgb[0] + 60 and rgb[2] > 100):
                        ffail("类型 chips 选中态不是强调色（实测 color={}），旧版用的是绿色".format(
                            f["chipOnColor"]))
                if f.get("chipHeights"):
                    # 触控目标：最窄一档（390px）在移动端媒体查询下会抬到 42px 上下
                    if width <= 480 and min(f["chipHeights"]) < 32:
                        ffail("移动端 chips 过矮（最小 {}px，< 32px 不好点）".format(
                            min(f["chipHeights"])))
                if not f.get("resetBtn") or f["resetBtn"]["w"] < 60:
                    ffail("缺少可用的「重置筛选」按钮：{}".format(f.get("resetBtn")))
                if "快捷筛选" not in f.get("capTitles", []):
                    ffail("筛选栏缺少「快捷筛选」分组标题栏（分层是这次改动的重点之一）")
                # 磨砂感不能被「加一层实底」破坏：面板仍必须是半透明的
                if f.get("panelAlpha", 1) > MAX_PANEL_ALPHA:
                    ffail("发现区面板底色不再半透明（alpha={} > {}），磨砂观感被实底遮掉".format(
                        f.get("panelAlpha"), MAX_PANEL_ALPHA))
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
            f_ = m.get("filters") or {}
            print("{:<26} 转存栏 top={:<5} h={:<3} 可点击={}  控件底色差={:<4} 字段图标={}/{}".format(
                m["label"], m["bar"]["t"], m["bar"]["h"], m["hitIsBar"],
                str(f_.get("bgDiff")), f_.get("groupIcons"), f_.get("groupCount")))
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
