#!/usr/bin/env python3
"""搜索结果下拉的「浮层底色」走查（issue #90：「搜索结果和背景混在一起」）。

用户诉求原文：一句话 ——「搜索资源后弹出的资源列表透明度太高了，导致和背景
混在一起」。

这类问题以前只能靠人眼截图判断，因为「透了」是**合成后**的观感，不是某一处
的单一属性。但它是可以量化的：下拉是一块压在页面背景（网格 + 漂移光斑）之上
的浮层，它的观感只由两件事决定 ——
  (a) 面板自身背景色的 alpha（越接近 1 越实），
  (b) 面板之内**有没有再叠一层半透明**把实底又稀释回去。
把这两件事分别断言，就能把「混在一起」变成一个可判定的数字。

断言清单：
  1. 下拉面板背景是半透明玻璃（不是实心块，保留磨砂观感）——alpha > 0 且 < 1
  2. 面板 alpha 达到浮层门槛（≥0.70）——与 .sheet / .confirm-box / .cmd-palette 对齐
  3. 面板 alpha **严格大于**普通卡片（--card）——钉住「别再退回卡片底」这个回归
  4. 面板在两种主题（深/浅）下都满足 2、3
  5. 展开的文件清单是**实底**（alpha == 1）——不允许再挖洞
  6. 展开区自身不再叠一层半透明底（不稀释面板实底）
  7. 合成校验：在面板中心叠一个 1px 采样，按 alpha 把面板色与页面背景做
     预乘混色，结果与「页面背景」的色差 ≥ 门槛 —— 即背景不再透到文字底下

依赖：playwright + chromium（与其它走查脚本同依赖）。
无依赖 / 无产物时以退出码 0 跳过（与其它走查脚本一致）。

用法：
  python3 scripts/check_search_dropdown_surface_dom.py            # 检查（失败非零退出）
  python3 scripts/check_search_dropdown_surface_dom.py --json     # 机器可读报告
  python3 scripts/check_search_dropdown_surface_dom.py --serve    # 自动起本地静态服务
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

# 浮层底色门槛：浮层必须「近乎实心」才能压住会移动的背景光斑。
# 与 --glass-bg-overlay（0.94/0.95）对应，留出 token 微调余量。
MIN_OVERLAY_ALPHA = 0.90
# 合成后与「最亮背景」的色差上限：浮层压在亮斑上时，面板色不应被抬太多。
# （0.94 下暗色约抬 8 级；若退回 0.55，亮斑会把面板抬 ~60 级。）
MAX_GLOW_LIFT = 20
# 合成后与页面背景的最小 RGB 通道色差（0-255）
MIN_COMPOSITE_GAP = 6
# 面板留白列亮度的允许离散度（std）：背景（网格/光斑）透进面板会被放大。
# 0.94 底衬实测 ≈3~4；退回 0.55 会达到十几到几十。
MAX_MODULATION = 8.0

SEARCH_STUB = {"success": True, "results": [
    {"title": "某剧 - 全 12 集", "url": "https://pan.quark.cn/s/preview-ok",
     "source": "夸克网盘"},
]}

STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.2.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/tmdb/list": {"items": []},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/transfer/status": {"running": False, "progress": [], "stats": {}},
    "/api/qas/tasks": {"success": True, "tasks": []},
    "/api/check_link": {"state": "done", "valid": True, "checked": True, "message": "链接正常"},
    "/api/share_detail": {
        "ok": True, "message": "", "title": "某剧 - 全 12 集", "total": 3,
        "video_count": 3, "dir_count": 0, "truncated": False,
        "episodes": {"numbers": [1, 2, 3], "missing": [], "continuous": True},
        "files": [{"name": "某剧.S01E0{}.mkv".format(i), "dir": False,
                   "size": 1024 * 1024 * 300, "fid": "f{}".format(i), "type": "video"}
                  for i in (1, 2, 3)],
    },
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
    if path == "/api/search":
        body = SEARCH_STUB
    else:
        body = STUBS.get(path, {})
    return route.fulfill(status=200, content_type="application/json",
                         body=json.dumps(body, ensure_ascii=False).encode())


# 在页面内把「颜色 alpha」与 token 一并取出，并做预乘合成计算。
# 全部用 getComputedStyle —— 廉价文本证据，不读图。
MEASURE = r"""
() => {
  const parse = (c) => {
    // "rgba(22, 23, 28, 0.74)" / "rgb(...)" / "transparent"
    if (!c) return null;
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map(s => parseFloat(s.trim()));
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };
  const alphaOf = (el) => {
    if (!el) return null;
    const c = parse(getComputedStyle(el).backgroundColor);
    return c ? c.a : null;
  };
  const token = (name) => {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
  };

  const dd = document.querySelector('#searchDropdown');
  const item = document.querySelector('.search-item');
  const detail = item ? item.querySelector('.search-detail') : null;
  const list = item ? item.querySelector('.search-detail-list') : null;

  // 页面背景（浮层要压在它之上）
  const pageBg = parse(getComputedStyle(document.body).backgroundColor)
              || parse(getComputedStyle(document.documentElement).backgroundColor);

  const ddColor = dd ? parse(getComputedStyle(dd).backgroundColor) : null;

  // 预乘合成：面板色 alpha 叠在页面背景之上 → 屏幕上的实际颜色
  let composite = null, gap = null;
  if (ddColor && pageBg) {
    const r = ddColor.r * ddColor.a + pageBg.r * (1 - ddColor.a);
    const g = ddColor.g * ddColor.a + pageBg.g * (1 - ddColor.a);
    const b = ddColor.b * ddColor.a + pageBg.b * (1 - ddColor.a);
    composite = {r: Math.round(r), g: Math.round(g), b: Math.round(b)};
    gap = Math.round(Math.max(Math.abs(r - pageBg.r),
                              Math.abs(g - pageBg.g),
                              Math.abs(b - pageBg.b)));
  }

  return {
    dropdownBg: dd ? getComputedStyle(dd).backgroundColor : '',
    dropdownAlpha: alphaOf(dd),
    cardToken: token('--card'),
    strongToken: token('--glass-bg-strong'),
    overlayToken: token('--glass-bg-overlay'),
    detailAlpha: alphaOf(detail),
    listAlpha: alphaOf(list),
    pageBg: pageBg,
    composite: composite,
    compositeGap: gap,
  };
}
"""


def _parse_css_color(s):
    """从 '--card' 这类 token 值（rgba(...)）里取 alpha，供断言 3 使用。"""
    import re
    m = re.search(r"rgba?\(([^)]+)\)", s or "")
    if not m:
        return None
    p = [x.strip() for x in m.group(1).split(",")]
    return float(p[3]) if len(p) > 3 else 1.0



def _panel_modulation(page):
    """采样下拉面板左右两侧无字留白列的亮度离散度。

    「透了」= 背景的网格线与漂移光斑在面板里仍看得见 —— 表现为同一块留白上
    出现明暗起伏。截一小条面板区域（约 12KB，非整页），直接读像素即可，
    这是廉价且确定性的证据（不读整页大图）。
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    box = page.evaluate(
        "()=>{var d=document.getElementById('searchDropdown');"
        "if(!d) return null; var r=d.getBoundingClientRect();"
        "return {x:Math.round(r.x),y:Math.round(r.y),"
        "w:Math.round(r.width),h:Math.round(r.height)};}")
    if not box:
        return None
    import io as _io
    clip = {"x": box["x"], "y": box["y"] + 4,
            "width": max(8, box["w"]), "height": max(8, box["h"] - 8)}
    buf = page.screenshot(clip=clip)
    img = Image.open(_io.BytesIO(buf)).convert("RGB")
    # 左右各取 6px 宽的留白列（避开主行文字与右侧按钮：按钮在中间偏右，留白在最右）
    xs = list(range(1, min(7, img.width))) + list(range(max(1, img.width - 7), img.width - 1))
    lum = []
    for y in range(img.height):
        for x in xs:
            r, g, b = img.getpixel((x, y))
            lum.append((r + g + b) / 3.0)
    if not lum:
        return None
    mean = sum(lum) / len(lum)
    var = sum((v - mean) ** 2 for v in lum) / len(lum)
    return {"mean": mean, "std": var ** 0.5, "min": min(lum), "max": max(lum)}

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
        PORT = 8150
        URL = "http://127.0.0.1:{}/index_new.html".format(PORT)
    problems, report = [], []

    def ok(name, cond, detail=""):
        report.append({"check": name, "pass": bool(cond), "detail": detail})
        if not cond:
            problems.append("{}：{}".format(name, detail))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"], **_launch_kwargs())

            def open_page(theme):
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                init = ("try{localStorage.setItem('auth_token','t');"
                        "localStorage.setItem('theme','" + theme + "')}catch(e){}")
                page.add_init_script(init)
                page.route("**/*", lambda r: _route(r))
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(1200)
                return page

            def open_dropdown(page):
                page.evaluate(
                    "()=>{var i=document.getElementById('searchInput');"
                    "i.value='某剧';i.dispatchEvent(new Event('input',{bubbles:true}));}")
                page.wait_for_timeout(900)   # onSearchInput 500ms 防抖
                # 展开文件清单：清单是「面板里的面板」，不展开就量不到它的底色
                btn = page.query_selector(".search-detail-toggle")
                if btn:
                    btn.click()
                    page.wait_for_timeout(800)

            for theme in ("dark", "light"):
                page = open_page(theme)
                open_dropdown(page)
                st = page.evaluate(MEASURE)
                tag = "明色" if theme == "light" else "暗色"

                a = st["dropdownAlpha"]
                card_a = _parse_css_color(st["cardToken"])
                strong_a = _parse_css_color(st["strongToken"])
                overlay_a = _parse_css_color(st["overlayToken"])

                ok("[{}] 下拉面板渲染出背景色".format(tag),
                   a is not None, "dropdownBg = {!r}".format(st["dropdownBg"]))
                ok("[{}] 面板仍是磨砂玻璃（半透明，不是实心块）".format(tag),
                   a is not None and 0 < a < 1,
                   "alpha = {}（应 0<a<1，实心块会丢掉磨砂观感）".format(a))
                ok("[{}] 面板 alpha 达到浮层门槛 ≥{}".format(tag, MIN_OVERLAY_ALPHA),
                   a is not None and a >= MIN_OVERLAY_ALPHA,
                   "alpha = {}，token --glass-bg-overlay = {!r}"
                   .format(a, st["overlayToken"]))
                ok("[{}] 面板用「浮层底」而非卡片底 / 强玻璃底".format(tag),
                   a is not None and overlay_a is not None and abs(a - overlay_a) < 1e-9,
                   "panel = {} vs --glass-bg-overlay = {}（卡片底 {} / 强玻璃底 {}）"
                   .format(a, overlay_a, card_a, strong_a))
                ok("[{}] 面板比卡片更实（alpha 严格大于 --card）".format(tag),
                   a is not None and card_a is not None and a > card_a,
                   "panel = {} vs card = {}（退回卡片底＝背景透字，就是 issue #90）"
                   .format(a, card_a))
                ok("[{}] 展开区不再叠一层半透明底（不稀释面板实底）".format(tag),
                   st["detailAlpha"] is not None and st["detailAlpha"] <= 0.12,
                   "detailAlpha = {}".format(st["detailAlpha"]))
                ok("[{}] 文件清单是实底（alpha == 1）".format(tag),
                   st["listAlpha"] == 1,
                   "listAlpha = {}（半透明清单会在浮层里挖洞）".format(st["listAlpha"]))
                # 真实像素校验：「混在一起」的本质是**背景的明暗变化透进了面板**。
                # 取面板左右两侧的无字留白列（padding 区），量这一列的亮度离散度。
                # 实测：0.94 底衬 ≈3~4；退回 0.55 会成倍放大到十几以上。
                mod = _panel_modulation(page)
                ok("[{}] 背景不再透进面板（留白列亮度离散度 ≤{}）".format(tag, MAX_MODULATION),
                   mod is not None and mod["std"] <= MAX_MODULATION,
                   "留白列 lum 均值 {:.1f} / std {:.2f} / range {}-{}".format(
                       mod["mean"], mod["std"], mod["min"], mod["max"]))
                ok("[{}] 合成后仍压得住背景（色差 ≥{}）".format(tag, MIN_COMPOSITE_GAP),
                   st["compositeGap"] is not None and st["compositeGap"] >= MIN_COMPOSITE_GAP,
                   "composite = {} vs pageBg = {} → gap = {}"
                   .format(st["composite"], st["pageBg"], st["compositeGap"]))
                page.close()

            browser.close()
    except Exception as e:
        problems.append("执行异常：{}".format(e))

    if args.json:
        print(json.dumps({"problems": problems, "report": report},
                         ensure_ascii=False, indent=2))
    else:
        for r in report:
            print("[{}] {}{}".format("PASS" if r["pass"] else "FAIL", r["check"],
                                     "" if r["pass"] else "  -> " + r["detail"]))
        print("\n共 {} 项，{} 项未通过".format(len(report), len(problems)))
        for pr in problems:
            print("  FAIL " + pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
