#!/usr/bin/env python3
"""TMDB 无封面占位卡片的 DOM 走查（issue #82「占位图错乱」）。

用途：
  用户在截图里指出海报墙上的「无封面占位图」显示错乱：渐变瓦片里
  图标缩在左上角、标题却居中，整块看上去「歪了」。
  这类错位在静态扫描里查不出来（CSS 里写着 align-items:center / gap:10px，
  读代码是「对的」），只有真在浏览器里量几何才能判定。

  本脚本把「占位图不乱」拆成可复算的几何断言，逐条钉住：

  1. 占位瓦片本身必须仍是 2:3 —— 与有封面的 <<img>> 同高。占位块比邻居矮或高，
     同一行就会「参差不齐」，这是「错乱」的第一层含义。
  2. 图标必须水平居中（与瓦片中心的偏差 ≤ 2px）。旧形态里图标锚在左上角，
     偏差会到上百像素。
  3. 标题必须水平居中，且与图标**同轴**（两者中心 x 差 ≤ 2px）。
     「图标在左、标题居中」正是 issue #82 截图里的画面。
  4. 图标必须保持 1:1 —— 被压成非等比的小块（实测 48x24）是同一根因的另一面。
  5. 内容整体必须垂直居中（图标中心相对瓦片 0.35~0.65）。旧形态内容贴着顶边，
     相对位置只有 0.04。
  6. 占位块不得复用 <<img>> 的尺寸类（不得同时带 .tmdb-poster），
     否则 aspect-ratio/object-fit/display 的归属又会变成隐式约定。

依赖：playwright + chromium（与 scripts/check_overview_dom.py 同依赖），
  pip install playwright && python -m playwright install chromium
无依赖时脚本以退出码 0 跳过（避免影响不装浏览器的 CI 阶段）。

用法：
  python3 scripts/check_tmdb_placeholder_dom.py            # 检查（失败非零退出）
  python3 scripts/check_tmdb_placeholder_dom.py --json     # 机器可读报告
  python3 scripts/check_tmdb_placeholder_dom.py --serve    # 自动起本地静态服务
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
PORT = 8129
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# 桩数据：5 张有封面 + 2 张无封面（无封面卡在第 2 行，便于量同一行的对齐）。
# 有封面的用 data: URL，避免依赖外网。
_PNG = ("data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAF"
        "BQEAXc6d7QAAAABJRU5ErkJggg==")
_ITEMS = [
    {"id": 100 + i, "title": "影片 %d" % i, "poster": _PNG, "rating": 7.5,
     "year": 2024, "votes": 1200, "overview": ""}
    for i in range(5)
]
_ITEMS += [
    {"id": 200, "title": "鬼玩人：炼狱", "poster": "", "rating": 7.7,
     "year": 2026, "votes": 1600, "overview": "爱丽丝在丈夫猝然离世后深陷悲痛。"},
    {"id": 201, "title": "怒之末 MUTINY", "poster": "", "rating": 6.4,
     "year": 2026, "votes": 900, "overview": "简介"},
]

STUBS = {
    "/api/tmdb/options": {
        "regions": [{"code": "US", "name": "美国"}],
        "languages": [{"code": "zh-CN", "name": "中文"}],
        "movie_list_types": [{"code": "trending", "name": "热门趋势"}],
        "tv_list_types": [{"code": "trending", "name": "热门趋势"}],
    },
    "/api/tmdb/list": {"items": _ITEMS, "total_pages": 1, "page": 1},
    "/api/tmdb/genres": {"genres": [{"id": 28, "name": "动作"}]},
    "/api/config": {"tmdb_api_key": "***"},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.1.0"},
    "/api/history": {"total": 0, "items": {}},
}

# ---------------- 判据阈值 ----------------
# 为什么是这些数：它们是「肉眼可见的错位」与「子像素抖动」的分界线。
# 居中类断言用 2px（大于等于 2px 的偏移在任何视口下都看得出来）；
# 比例类用 2% 容差（浏览器对 aspect-ratio 的亚像素舍入）。
CENTER_TOL_PX = 2.0        # 水平居中允许的偏差
AXIS_TOL_PX = 2.0          # 图标与标题的轴线偏差
RATIO_TOL = 0.02           # 2:3 与 1:1 的比例容差
V_CENTER_MIN = 0.35        # 内容垂直居中的下界（相对瓦片高度）
V_CENTER_MAX = 0.65        # 上界

MEASURE = r"""
() => {
  const cards = [...document.querySelectorAll('#tmdbGrid .tmdb-card')];
  const out = {cards: cards.length, tiles: []};
  const R = el => { const b = el.getBoundingClientRect();
                    return {x: b.x, y: b.y, w: b.width, h: b.height}; };

  cards.forEach(c => {
    const wrap = c.querySelector('.tmdb-poster-wrap');
    const np = c.querySelector('.tmdb-no-poster');
    if (!wrap) return;
    const wr = R(wrap);
    const item = {
      title: (c.querySelector('.tmdb-title') || {}).textContent || '',
      isPlaceholder: !!np,
      wrap: wr,
      // 占位块不得复用 <img> 的尺寸类：复用会让 aspect-ratio/object-fit 的归属
      // 变成两个单类选择器之间的隐式竞争
      sharesImgClass: !!np && np.classList.contains('tmdb-poster'),
    };
    if (np) {
      const nr = R(np);
      const svg = np.querySelector('svg');
      const t = np.querySelector('.tmdb-no-poster-title');
      item.np = nr;
      if (svg) {
        const sr = R(svg);
        item.svg = sr;
        item.svgRatio = sr.h ? sr.w / sr.h : 0;
        item.svgCx = sr.x + sr.w / 2;
        item.svgCy = sr.y + sr.h / 2;
        item.svgCxRel = wr.w ? (item.svgCx - wr.x) / wr.w : 0;
        item.svgCyRel = wr.h ? (item.svgCy - wr.y) / wr.h : 0;
        item.svgInlineW = svg.style.width || '';
        item.svgCssW = getComputedStyle(svg).width;
        item.svgCssH = getComputedStyle(svg).height;
      }
      if (t) {
        const tr = R(t);
        item.title_rect = tr;
        item.titleCx = tr.x + tr.w / 2;
        item.titleCxRel = wr.w ? (item.titleCx - wr.x) / wr.w : 0;
      }
      item.tileRatio = nr.w && nr.h ? nr.w / nr.h : 0;
      item.wrapRatio = wr.w && wr.h ? wr.w / wr.h : 0;
    }
    out.tiles.push(item);
  });
  return out;
}
"""

# (宽度, 高度, 标签) —— 覆盖 5/4/3/2 列断点，确保各种瓦片宽度下居中都不漂
VIEWPORTS = [
    (2560, 1392, "2560x1392 超宽屏"),
    (1440, 900, "1440x900"),
    (1200, 900, "1200x900 四列"),
    (900, 900, "900x900 三列边界"),
    (760, 900, "760x900"),
    (390, 844, "390x844 移动端两列"),
]


def _launch(p):
    """起 chromium：优先用 Playwright 自带解析，找不到可执行文件时退回显式路径。

    本仓库的 CI 镜像里 headless_shell 可能没装、只装了完整 chromium，
    与 scripts/check_discovery_dom.py 等脚本保持同样的兜底口径。
    """
    try:
        return p.chromium.launch(args=["--no-sandbox"])
    except Exception:
        import glob
        for pat in ("/opt/ms-playwright/chromium-*/chrome-linux64/chrome",
                    "/opt/ms-playwright/chromium-*/chrome-linux/chrome"):
            hits = sorted(glob.glob(pat))
            if hits:
                return p.chromium.launch(executable_path=hits[-1],
                                         args=["--no-sandbox"])
        raise


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
    ap.add_argument("--serve", action="store_true", help="自动起本地静态服务")
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
            browser = _launch(p)
            for width, height, label in VIEWPORTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", handler)
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(1100)
                page.evaluate("()=>{ try{ jumpToDiscovery(); }catch(e){} }")
                page.wait_for_timeout(1000)

                m = page.evaluate(MEASURE)
                rec = {"label": label, "width": width, "height": height,
                       "placeholders": 0}
                holders = [t for t in m.get("tiles", []) if t.get("isPlaceholder")]
                rec["placeholders"] = len(holders)
                rec["samples"] = []

                def fail(msg):
                    problems.append("[{}] {}".format(label, msg))

                if not m.get("tiles"):
                    fail("海报墙没有渲染出任何卡片")
                    report.append(rec)
                    page.close()
                    continue
                if not holders:
                    fail("没有渲染出无封面占位卡片（桩数据里第 6/7 条是无封面的）")
                    report.append(rec)
                    page.close()
                    continue

                for t in holders:
                    name = "占位卡「{}」".format(t["title"] or "?")
                    s = {
                        "title": t["title"],
                        "tileRatio": round(t.get("tileRatio", 0), 4),
                        "svgRatio": round(t.get("svgRatio", 0), 4),
                        "svgCxRel": round(t.get("svgCxRel", 0), 4),
                        "svgCyRel": round(t.get("svgCyRel", 0), 4),
                        "titleCxRel": round(t.get("titleCxRel", 0), 4),
                        "svgCssW": t.get("svgCssW"), "svgCssH": t.get("svgCssH"),
                    }
                    rec["samples"].append(s)

                    # 1. 瓦片仍是 2:3（与 <img> 同高，同一行不参差）
                    if abs(t.get("tileRatio", 0) - 2.0 / 3.0) > RATIO_TOL:
                        fail("{} 瓦片比例 {}（应约 0.6667）—— 占位块与有封面的卡不同高，"
                             "同一行会参差不齐".format(name, round(t.get("tileRatio", 0), 3)))

                    # 6. 不得复用 <img> 的尺寸类
                    if t.get("sharesImgClass"):
                        fail("{} 同时带 .tmdb-poster（<img> 的尺寸类）—— "
                             "aspect-ratio/object-fit/display 的归属会变成隐式竞争".format(name))

                    svg, ttl, wr = t.get("svg"), t.get("title_rect"), t.get("wrap")
                    if not svg or not ttl:
                        fail("{} 缺少图标或标题节点".format(name))
                        continue

                    # 4. 图标必须 1:1
                    if abs(t.get("svgRatio", 0) - 1.0) > RATIO_TOL:
                        fail("{} 图标被压成非等比（{}x{}，比例 {}）—— 应保持 1:1".format(
                            name, round(svg["w"]), round(svg["h"]), round(t.get("svgRatio", 0), 3)))

                    # 2. 图标水平居中
                    svg_cx = svg["x"] + svg["w"] / 2
                    wrap_cx = wr["x"] + wr["w"] / 2
                    if abs(svg_cx - wrap_cx) > CENTER_TOL_PX:
                        fail("{} 图标未水平居中：图标中心 {:.0f} vs 瓦片中心 {:.0f}"
                             "（偏 {:.0f}px）—— 就是「图标缩在左边」".format(
                                 name, svg_cx, wrap_cx, abs(svg_cx - wrap_cx)))

                    # 3. 标题水平居中，且与图标同轴
                    ttl_cx = ttl["x"] + ttl["w"] / 2
                    if abs(ttl_cx - wrap_cx) > CENTER_TOL_PX:
                        fail("{} 标题未水平居中：标题中心 {:.0f} vs 瓦片中心 {:.0f}"
                             "（偏 {:.0f}px）".format(name, ttl_cx, wrap_cx, abs(ttl_cx - wrap_cx)))
                    if abs(svg_cx - ttl_cx) > AXIS_TOL_PX:
                        fail("{} 图标与标题不同轴：图标中心 x={:.0f}，标题中心 x={:.0f}"
                             "（差 {:.0f}px）—— 这就是截图里「图标在左、标题居中」".format(
                                 name, svg_cx, ttl_cx, abs(svg_cx - ttl_cx)))

                    # 5. 内容整体垂直居中
                    cy_rel = t.get("svgCyRel", 0)
                    if not (V_CENTER_MIN <= cy_rel <= V_CENTER_MAX):
                        fail("{} 内容未垂直居中：图标中心相对瓦片高 {:.3f}"
                             "（应在 {}~{}）".format(name, cy_rel, V_CENTER_MIN, V_CENTER_MAX))

                report.append(rec)
                page.close()
            browser.close()
    finally:
        if httpd is not None:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report},
                         ensure_ascii=False, indent=2))
    else:
        for r in report:
            print("{:<22} 占位卡 {} 张".format(r["label"], r["placeholders"]))
            for s in r.get("samples", []):
                print("    「{}」 瓦片比={} 图标比={} 图标CX={} 图标CY={} 标题CX={}".format(
                    s["title"], s["tileRatio"], s["svgRatio"],
                    s["svgCxRel"], s["svgCyRel"], s["titleCxRel"]))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p_ in problems:
                print("  - " + p_)
            return 1
        print("TMDB 无封面占位卡 DOM 走查通过（{} 组视口，0 处问题）".format(len(report)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
