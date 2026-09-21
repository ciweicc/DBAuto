#!/usr/bin/env python3
"""TMDB 无封面占位卡片的 DOM 走查（issue #82「占位图错乱」+ issue #86「占位符太丑」）。

用途：
  issue #82：用户在截图里指出海报墙上的「无封面占位图」显示错乱：渐变瓦片里
  图标缩在左上角、标题却居中，整块看上去「歪了」。
  这类错位在静态扫描里查不出来（CSS 里写着 align-items:center / gap:10px，
  读代码是「对的」），只有真在浏览器里量几何才能判定。

  issue #86：用户接着说「大量海报加载失败，而且失败时的占位符太丑」。
  「丑」同样只能量出来 —— 它由几个可度量的量决定：背景与页面底色的色差、
  图标的实际不透明度、标题的对比度与是否还挂着黑描边。本脚本为此补了两组
  断言（第 7~10 条），并把「加载失败」与「本来就没有封面」区分开：
  后者直接渲染占位，前者要先重试一次再降级（重试路径见第 10 条）。

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

  —— 以下是 issue #86「占位符太丑」补的判据 ——

  7. 占位底色必须**跟随主题**且与页面底色同色系：底色必须来自
     --tmdb-poster-bg 令牌（不得写死一组颜色），且与页面底色的
     **HSV 彩度差**不得超过 0.10。
     为什么不用色相：旧配色 #1a1a2e 与页面底 #0d0d10 的色相都是 240°，
     色相差为 0 —— 只比色相的断言会全绿放行，而屏幕上那块蓝紫瓦片与近黑页面
     明明是两个世界。差别在彩度：0.44 vs 0.19。「彩色 vs 中性」才是
     「异色补丁」的准确描述。
  8. 图标必须可见：计算不透明度 ≥ .8、渲染尺寸 ≥ 48px。
     旧形态 opacity 只有 .35，实测在深底上几乎看不见。
  9. 标题不得带描边阴影（text-shadow 必须是 none）——
     旧形态挂着 rgba(0,0,0,.55) 的 3px 投影，在浅色主题下会糊成一圈灰晕。
 10. 「加载失败」必须被兜住，而不是留破图：
     桩数据里放一条**首次 500、重试 200** 的图片，断言它最终**加载成功**
     （占位卡计数不增加、img 的 src 带上重试标记）；
     再放一条**永远 404** 的，断言它降级成占位卡且被标记 .tmdb-poster-failed，
     两张卡的占位观感与「本来就没封面」的那张完全一致
     （同底色、同字号——否则同一屏里会出现三种「没有封面」的样子）。

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
# issue #86：两条「有 poster_path 但图取不到」的卡。
#   FLAKY_* → 首次 500、带 tmdb_retry 标记的再请求 200（前端重试一次应当救回来）
#   DEAD_*  → 永远 404（重试也救不回，应降级成占位卡并被标记）
# 用图片名的前缀区分（走查脚本的 route 拦截按前缀决定返回什么），
# 域名仍走真实 image.tmdb.org，避免为「图片」再引入一套假域名。
_FLAKY_URL = "https://image.tmdb.org/t/p/w300/FLAKY_placeholder_check.jpg"
_DEAD_URL = "https://image.tmdb.org/t/p/w300/DEAD_placeholder_check.jpg"
_ITEMS += [
    {"id": 300, "title": "抖动后可恢复", "poster": _FLAKY_URL, "rating": 9.0,
     "year": 2025, "votes": 900, "overview": "简介"},
    {"id": 301, "title": "图挂了", "poster": _DEAD_URL, "rating": 4.2,
     "year": 2025, "votes": 300, "overview": "简介"},
]
# 1x1 的占位 JPEG：FLAKY 重试成功后返回它（内容不重要，能解码即可）
_OK_PNG = _PNG
# 同一张 1x1 PNG 的裸字节：route.fulfill 需要 bytes 而不是 data URL
import base64 as _b64
_PNG_BYTES = _b64.b64decode(_PNG.split(",", 1)[1])

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

# ---- issue #86「占位符太丑」的阈值 ----
# 判「异色补丁」**不能只看色相**，这是本轮踩的坑：
#   旧底色 #1a1a2e 的色相是 240°，页面底色 #0d0d10 的色相也是 240°，
#   色相差为 0 —— 只比色相的断言会**全绿放行**，而屏幕上那块蓝紫瓦片
#   与近黑页面明明是两个世界。
#   差别在**彩度**：#1a1a2e 的 HSV 饱和度 43%（一眼是「蓝紫色」），
#   #0d0d10 只有 19%（读起来就是「黑」）。
# 因此改用 HSV 彩度之差（CHROMA_TOL）为主判据，色相只作兜底：
#   近中性色（彩度 < NEUTRAL_CHROMA）不参与色相比对，避免灰对灰报噪声。
CHROMA_TOL = 0.10          # HSV 饱和度允许的差值（旧形态实测差 0.25，修复后 0.06）
NEUTRAL_CHROMA = 0.20      # 低于此彩度视为「中性灰」，不参与色相比对
HUE_TOL_DEG = 40.0         # 双方都有彩度时的色相兜底阈值
SVG_OPACITY_MIN = 0.8      # 图标计算不透明度下限（旧形态 .35）
SVG_SIZE_MIN = 48          # 图标渲染边长下限 px（旧形态 64，但 opacity 太低）
REQUIRED_TOKENS = ("--tmdb-poster-bg", "--tmdb-poster-ink")

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

      // issue #86：占位观感的可度量部分
      const npcs = getComputedStyle(np);
      const svgcs = svg ? getComputedStyle(svg) : null;
      const tcs = t ? getComputedStyle(t) : null;
      item.bgImage = npcs.backgroundImage;
      item.svgOpacity = svgcs ? parseFloat(svgcs.opacity) : null;
      item.svgColor = svgcs ? svgcs.color : null;
      item.titleShadow = tcs ? tcs.textShadow : null;
      item.titleFontSize = tcs ? parseFloat(tcs.fontSize) : null;
      // 装饰层必须真的画出来了（格纹 + 光斑），否则令牌没生效
      item.hasGrid = /gradient/.test(getComputedStyle(np, '::before').backgroundImage || '');
      item.hasGlow = /gradient/.test(getComputedStyle(np, '::after').backgroundImage || '');
    }
    // 真实 <img> 的状态：用来判定「重试救回来了没有」
    const img = c.querySelector('img.tmdb-poster');
    item.img = img ? {
      src: img.currentSrc || img.src || '',
      complete: img.complete,
      naturalW: img.naturalWidth,
      retried: !!img.getAttribute('data-tmdbRetry'),
    } : null;
    item.posterFailed = c.classList.contains('tmdb-poster-failed');
    out.tiles.push(item);
  });
  return {
    cards: out.cards,
    tiles: out.tiles,
    // 主题令牌：遮罩/底色断言都从渲染值取，不读源码，避免「源码改了但产物没重建」
    vars: {
      bg: getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
      posterBg: getComputedStyle(document.documentElement).getPropertyValue('--tmdb-poster-bg').trim(),
      posterInk: getComputedStyle(document.documentElement).getPropertyValue('--tmdb-poster-ink').trim(),
    },
  };
}
"""

def _parse_rgb(text):
    """从颜色字符串里取出 (r, g, b)。

    两个来源都要吃：
      - 计算样式里的 'rgb(27, 30, 41)' / 'rgba(0, 0, 0, 0.6)'；
      - 令牌原文里的 '#1b1e29' / '#fff'（--bg 就是这样，getPropertyValue
        返回的是原文而不是计算后的颜色）。
    早期版本只认 rgb()，于是 `--bg: #0d0d10` 解析成 None，色相断言被静默跳过 ——
    这正是「断言写了但没生效」的典型（连 fail 都没触发，因为上层只判「取不到就报错」）。
    """
    import re as _re
    t = (text or "").strip()
    m = _re.search(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", t)
    if m:
        return tuple(float(m.group(i)) for i in (1, 2, 3))
    m = _re.search(r"#([0-9a-fA-F]{3,8})\b", t)
    if not m:
        return None
    h = m.group(1)
    if len(h) in (3, 4):          # #abc / #abcd
        h = "".join(c * 2 for c in h[:3])
    elif len(h) in (6, 8):        # #aabbcc / #aabbccdd
        h = h[:6]
    else:
        return None
    return (float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)))


def _first_rgb_in(text):
    """从渐变字符串里取第一组颜色（占位底色的「起手色」）。

    background-image 形如 'linear-gradient(168deg, rgb(27, 30, 41) 0%, ...)'，
    第一个 rgb() 就是渐变的起点 —— 它决定了这块瓦片的整体色调。
    """
    return _parse_rgb(text)


def _hue(rgb):
    r, g, b = [v / 255.0 for v in rgb]
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        return 0.0
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0


def _saturation(rgb):
    """HSV 饱和度（0~1）。判「这块颜色读起来是不是彩色」用的就是它。"""
    r, g, b = [v / 255.0 for v in rgb]
    mx, mn = max(r, g, b), min(r, g, b)
    return 0.0 if mx == 0 else (mx - mn) / mx


def _hue_delta(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# (宽度, 高度, 主题, 标签) —— 覆盖 5/4/3/2 列断点，确保各种瓦片宽度下居中都不漂。
#
# 为什么要带主题：issue #86 的「丑」在深浅两套主题下是**两个不同的病** ——
# 深色下是与近黑页面底色的彩度落差（异色补丁），浅色下是那块深蓝紫瓦片
# 直接变成整屏最重的色块。只测一套主题等于只治了一半。
VIEWPORTS = [
    (2560, 1392, "dark", "2560x1392 超宽屏"),
    (1440, 900, "dark", "1440x900"),
    (1200, 900, "dark", "1200x900 四列"),
    (900, 900, "dark", "900x900 三列边界"),
    (760, 900, "dark", "760x900"),
    (390, 844, "dark", "390x844 移动端两列"),
    (1440, 900, "light", "1440x900 浅色"),
    (390, 844, "light", "390x844 移动端两列 浅色"),
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

    # issue #86：图片链路要能造出两种「加载失败」。
    #   DEAD_*  → 永远 404（重试也救不回）
    #   FLAKY_* → 首次 500；带 tmdb_retry 再请求时 200（前端重试一次应当救回来）
    # 用独立计数器统计每个 URL 被请求了几次 —— 这就是「重试真的发生了」的证据。
    img_hits = {}
    # 每个视口的请求计数基线（见下方 flaky_base 的用法）
    flaky_base = [0]

    def handler(route):
        url = route.request.url
        if "image.tmdb.org" in url and "DEAD_" in url:
            img_hits[url] = img_hits.get(url, 0) + 1
            route.fulfill(status=404, content_type="text/plain", body=b"not found")
            return
        if "image.tmdb.org" in url and "FLAKY_" in url:
            img_hits[url] = img_hits.get(url, 0) + 1
            if "tmdb_retry" not in url:
                route.fulfill(status=500, content_type="text/plain", body=b"boom")
            else:
                route.fulfill(status=200, content_type="image/png", body=_PNG_BYTES)
            return
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
            for width, height, theme, label in VIEWPORTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script(
                    "try{localStorage.setItem('auth_token','t');"
                    "localStorage.setItem('theme','%s')}catch(e){}" % theme)
                page.route("**/*", handler)
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(1100)
                page.evaluate("()=>{ try{ jumpToDiscovery(); }catch(e){} }")
                # 给「首次失败 → 重试 → 成功/降级」这条链路留足时间（重试是真实网络往返）
                page.wait_for_timeout(2600)

                m = page.evaluate(MEASURE)
                rec = {"label": label, "theme": theme, "width": width,
                       "height": height, "placeholders": 0}
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
                        "svgOpacity": t.get("svgOpacity"),
                        "titleShadow": t.get("titleShadow"),
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

                    # ---- issue #86「占位符太丑」 ----

                    # 7a. 图标必须真的看得见：计算不透明度 + 渲染边长
                    op = t.get("svgOpacity")
                    if op is None or op < SVG_OPACITY_MIN:
                        fail("{} 图标不透明度 {}（应 ≥ {}）—— "
                             "旧形态只有 .35，在深底上几乎看不见".format(
                                 name, op, SVG_OPACITY_MIN))
                    if min(svg["w"], svg["h"]) < SVG_SIZE_MIN:
                        fail("{} 图标渲染尺寸 {}x{} 小于 {}px，缩得太小就只剩一个点".format(
                            name, round(svg["w"]), round(svg["h"]), SVG_SIZE_MIN))

                    # 9. 标题不得带描边阴影
                    sh = (t.get("titleShadow") or "none").strip()
                    if sh not in ("none", ""):
                        fail("{} 标题仍带 text-shadow（{}）—— "
                             "浅色主题下会糊成一圈灰晕，是「丑」的一部分".format(name, sh))

                    # 7b. 装饰层（胶片格纹 + 径向光斑）必须真的画出来了
                    if not t.get("hasGrid"):
                        fail("{} 未渲染占位格纹（--tmdb-poster-grid 未生效）".format(name))
                    if not t.get("hasGlow"):
                        fail("{} 未渲染占位光斑（--tmdb-poster-glow 未生效）".format(name))

                # 8. 占位底色必须跟随主题、与页面底色同色系
                vars_ = m.get("vars", {}) or {}
                # 令牌名在 MEASURE 里是以「去掉 -- 前缀」的键取回的，
                # 这里统一翻回原样，避免「改了断言里的写法就静默失效」。
                tok_vals = {
                    "--tmdb-poster-bg": vars_.get("posterBg", ""),
                    "--tmdb-poster-ink": vars_.get("posterInk", ""),
                }
                rec["tokens"] = tok_vals
                for tok in REQUIRED_TOKENS:
                    if not tok_vals.get(tok):
                        fail("主题缺少令牌 {}（占位观感必须由令牌驱动，"
                             "不得在 CSS 里写死一组颜色）".format(tok))
                page_bg = _parse_rgb(vars_.get("bg", ""))
                rec["pageBg"] = vars_.get("bg", "")
                if page_bg:
                    rec["pageChroma"] = round(_saturation(page_bg), 3)
                for t in holders:
                    name = "占位卡「{}」".format(t["title"] or "?")
                    tile_bg = _first_rgb_in(t.get("bgImage", ""))
                    if not tile_bg or not page_bg:
                        fail("{} 取不到占位底色 / 页面底色（{} vs {}）".format(
                            name, t.get("bgImage"), vars_.get("bg")))
                        continue
                    t_chroma = _saturation(tile_bg)
                    p_chroma = _saturation(page_bg)
                    rec.setdefault("chroma", []).append(
                        {"title": t["title"], "tile": round(t_chroma, 3),
                         "page": round(p_chroma, 3)})
                    # 主判据：彩度必须与页面底色同档 —— 这才是「异色补丁」的正解
                    if abs(t_chroma - p_chroma) > CHROMA_TOL:
                        fail("{} 底色彩度 {:.2f} 与页面底色 {:.2f} 相差 {:.2f}（>{:.2f}）—— "
                             "页面底是近黑中性色而瓦片是带彩的蓝紫，整屏看过去就是"
                             "一块「异色补丁」，这正是「丑」的第一层".format(
                                 name, t_chroma, p_chroma,
                                 abs(t_chroma - p_chroma), CHROMA_TOL))
                    # 兜底：双方都有彩度时，再看一眼色相有没有跑偏
                    if t_chroma >= NEUTRAL_CHROMA and p_chroma >= NEUTRAL_CHROMA:
                        d = _hue_delta(_hue(tile_bg), _hue(page_bg))
                        if d > HUE_TOL_DEG:
                            fail("{} 底色色相相对页面底色偏出 {:.0f}°（>{:.0f}°）".format(
                                name, d, HUE_TOL_DEG))

                # 10. 「加载失败」必须被兜住，而不是留破图
                by_title = {t["title"]: t for t in m.get("tiles", [])}
                alive = by_title.get("抖动后可恢复")
                if alive is None:
                    fail("桩数据里的「抖动后可恢复」卡没有渲染")
                else:
                    # 首次 500、重试 200 —— 最终必须是**真图**，不是占位卡
                    if alive.get("isPlaceholder"):
                        fail("「抖动后可恢复」在重试后仍退化成占位卡 —— "
                             "瞬时抖动被当成了「没有封面」，用户会白白少一张海报")
                    img = alive.get("img") or {}
                    if not img.get("complete") or not img.get("naturalW"):
                        fail("「抖动后可恢复」的 <img> 没有加载成功（complete={}, naturalW={}）".format(
                            img.get("complete"), img.get("naturalW")))
                    if not img.get("retried"):
                        fail("「抖动后可恢复」没有走重试路径（img 上没有 data-tmdbRetry 标记）"
                             "—— 说明首跳失败就立刻降级了")
                    rec["retriedSrc"] = img.get("src", "")

                # 永远 404 的那张：必须降级成占位卡，且带上失败标记
                dead = by_title.get("图挂了")
                if dead is None:
                    fail("桩数据里的「图挂了」卡没有渲染")
                else:
                    if not dead.get("isPlaceholder"):
                        fail("「图挂了」没有降级成占位卡 —— 页面上会留一个破图")
                    if not dead.get("posterFailed"):
                        fail("「图挂了」的卡片没有 .tmdb-poster-failed 标记 —— "
                             "「图挂了」与「本来没封面」在 DOM 上又混成了一种")

                # 三种占位（没封面 / 图挂了）观感必须一致：同一屏不该有三种「没有封面」
                plain = by_title.get("鬼玩人：炼狱")
                if plain and dead and plain.get("isPlaceholder") and dead.get("isPlaceholder"):
                    if (plain.get("bgImage") or "") != (dead.get("bgImage") or ""):
                        fail("「本来没封面」与「图挂了」两种占位卡底色不一致：\n"
                             "    没封面 = {}\n    图挂了 = {}".format(
                                 plain.get("bgImage"), dead.get("bgImage")))
                    if abs((plain.get("titleFontSize") or 0) - (dead.get("titleFontSize") or 0)) > 0.5:
                        fail("两种占位卡标题字号不一致（{} vs {}）—— "
                             "同一屏出现两种「没有封面」的样子".format(
                                 plain.get("titleFontSize"), dead.get("titleFontSize")))

                # 重试必须真的发生了网络往返（否则「重试」只是改了 src）。
                # img_hits 是整个浏览器进程共享的计数器，直接读会跨视口累加 ——
                # 那样「只请求了 1 次」的坏情况会被别的视口垫成 2 次而漏报。
                # 因此记下本视口开始前的基线，只算增量。
                flaky_now = sum(v for k, v in img_hits.items() if "FLAKY_" in k)
                flaky_hits = flaky_now - flaky_base[0]
                flaky_base[0] = flaky_now
                if flaky_hits < 2:
                    fail("「抖动后可恢复」在本视口只被请求了 {} 次 —— "
                         "重试路径没有真正发起第二次请求".format(flaky_hits))
                rec["imgHits"] = flaky_hits

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
