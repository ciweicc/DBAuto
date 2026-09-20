#!/usr/bin/env python3
"""TMDB 发现区筛选栏 DOM 走查（issue #15 第 5 轮）。

用途：
  「筛选框 UI 太素」是一个主观描述，本脚本把它拆成**可量化、可复现**的断言，
  在真实浏览器里加载构建产物、注入确定性的 TMDB 桩数据后逐条测量：

  A. 视觉层次（这是「素」的对立面，也是最容易被静默改回去的部分）
     A1. 筛选控件底色必须与它所在面板底色**不同** ——
         旧版两者几乎同色（通道均差实测 4），控件「融进」面板正是「素」的根因；
     A2. 控件圆角 ≥ 8px（旧版 4px，与 10px 的面板圆角不协调，像表格格子）；
     A3. 每个筛选字段都必须带图标（8/8；纯文字标签是「素」的另一半）；
     A4. 高级筛选整块必须有独立内层底卡（与面板底色不同）；
     A5. 类型 chips 必须是胶囊（圆角 ≥ 100px），选中态必须落在强调色系（蓝），
         旧版用的是绿色 (--green)，与页面主色不是一套；
     A6. 移动端 chips 不得过矮（≥32px，触控目标）；
     A7. 「高级筛选」标题行必须整体落在内层底卡的内边距流内 —— 标题
         不得被底卡上边框横切（issue #24：fieldset > legend 会被 UA 样式提到
         fieldset 边框上排版，实测顶边相对 fieldset 为 0px，而普通子元素是
         +46px，于是面板上边框从标题中间穿过，观感即「字体重叠」）。
     A8. 「仅「发现」榜单有效」这条冗余提示不得回来（issue #57 追问）——
         用户在截图里指出「这个提示有点多余了」。判据两条：DOM 里不存在
         `.tmdb-adv-hint`，且底卡可见文本里不得出现「仅…榜单有效」一类措辞。
  B. 结构（分层与命名）
     B1. 模块标题必须是「TMDB 发现」（用户明确要求重命名）；
     B2. 筛选栏必须有「快捷筛选」分组标题栏（分层是这次改动的重点之一）；
     B3. 必须有可用的「重置筛选」按钮。
  C. 副作用与语义
     C1. 面板底色必须仍是半透明（alpha ≤ 0.6）—— 本轮视觉升级不得靠「加一层
         不透明底」实现，那会把面板的磨砂观感遮掉；
     C2. 重置按钮必须真的把 9 项筛选条件还原为默认，且选中态（chips/平台多选）
         同步回默认 —— 只改 value 不改样式的「假重置」会被这条拦住。

依赖：playwright + chromium（与 scripts/check_overview_dom.py 同依赖），
  pip install playwright && python -m playwright install chromium
无依赖时脚本以退出码 0 跳过（避免影响不装浏览器的 CI 阶段）。

用法：
  python3 scripts/check_tmdb_filters_dom.py            # 检查（失败非零退出）
  python3 scripts/check_tmdb_filters_dom.py --json     # 机器可读报告
  python3 scripts/check_tmdb_filters_dom.py --serve    # 自动起本地静态服务
"""
import argparse
import functools
import http.server
import json
import os
import re
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "static", "index_new.html")
PORT = 8127
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# ---------------------------------------------------------------- 阈值
MIN_SELECT_RADIUS = 8      # 筛选控件圆角下限（px）
MIN_BG_DIFF = 4            # 控件底色 vs 面板底色的 RGB 通道均差下限
MIN_ADV_BG_DIFF = 3        # 高级筛选底卡 vs 面板底色的通道均差下限
MIN_CHIP_RADIUS = 100      # 类型 chips 胶囊化下限（px，999px 也满足）
MIN_CHIP_TOUCH = 32        # 移动端 chips 触控高度下限（px）
MAX_PANEL_ALPHA = 0.6      # 面板底色 alpha 上限（保持磨砂，禁止实底遮罩）
MAX_BAR_H = 66             # 筛选栏字段行高度上限（超过说明被挤成两行，对齐失败）
MIN_RESET_W = 60           # 重置按钮最小宽度（px）
# 标题行相对底卡内容盒顶边的溢出上限（px）。>0 即被上边框横切；留 0.5px 抗亚像素。
MAX_LEGEND_OVERFLOW = 0.5

# ---------------------------------------------------------------- 桩数据
# 与 app_modules/tmdb.py 的 MOVIE_LIST_TYPES / TV_LIST_TYPES 契约一致。
# 必须含 discover：高级筛选的可用态由 list_type=discover 驱动。
_ITEMS = [
    {"id": 100 + i, "title": "影片 %d" % i, "poster": "", "rating": 7.5,
     "year": 2024, "votes": 1200, "overview": ""}
    for i in range(12)
]
STUBS = {
    "/api/tmdb/options": {
        "regions": [{"code": "US", "name": "美国"}, {"code": "CN", "name": "中国大陆"}],
        "languages": [{"code": "zh-CN", "name": "中文"}, {"code": "en-US", "name": "英语"}],
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

MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const qa = s => [...document.querySelectorAll(s)];
  const parse = c => {
    const m = String(c || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x.trim()));
    return {r: p[0] || 0, g: p[1] || 0, b: p[2] || 0, a: p.length > 3 ? p[3] : 1};
  };
  const diff = (a, b) => a && b
    ? Math.round((Math.abs(a.r - b.r) + Math.abs(a.g - b.g) + Math.abs(a.b - b.b)) / 3) : null;
  // 半透明色与深色底合成后的「实际观感色」（深色主题的页面底接近 #0d0d10）
  const flat = (c, bg) => c.a >= 1 ? c
    : {r: Math.round(c.r * c.a + bg * (1 - c.a)),
       g: Math.round(c.g * c.a + bg * (1 - c.a)),
       b: Math.round(c.b * c.a + bg * (1 - c.a))};
  const radius = el => el ? parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0 : 0;

  const panel = q('.ov-panel-discovery');
  const panelBg = panel ? parse(getComputedStyle(panel).backgroundColor) : null;
  const selects = qa('.tmdb-filters .tmdb-select');
  const groups = qa('.tmdb-filters .tmdb-filter-group');
  const chips = qa('#tmdbGenreChips .chip');
  const chipOn = chips.find(c => c.classList.contains('on')) || null;
  const adv = q('#tmdbAdv');
  const advBg = adv ? parse(getComputedStyle(adv).backgroundColor) : null;
  const reset = q('#tmdbResetBtn');
  const resetR = reset ? reset.getBoundingClientRect() : null;

  // 控件底色与面板底色在**同一口径**下比较：
  // 两者都先与页面底色（深色主题 ~ #0c0c0f）合成，再取通道均差。
  const PAGE_DARK = 12;
  const selectBg = selects.length ? parse(getComputedStyle(selects[0]).backgroundColor) : null;
  const bgDiff = (selectBg && panelBg)
    ? diff(flat(selectBg, PAGE_DARK), flat(panelBg, PAGE_DARK)) : null;
  const advBgDiff = (advBg && panelBg)
    ? diff(flat(advBg, PAGE_DARK), flat(panelBg, PAGE_DARK)) : null;

  return {
    panelTitle: (() => { const t = q('#tmdbPanelTitle'); return t ? t.textContent.trim() : null; })(),
    selectCount: selects.length,
    selectBg: selects.length ? getComputedStyle(selects[0]).backgroundColor : null,
    selectRadius: radius(selects[0]),
    bgDiff: bgDiff,
    panelBg: panel ? getComputedStyle(panel).backgroundColor : null,
    panelAlpha: panelBg ? panelBg.a : 1,
    groupCount: groups.length,
    groupIcons: groups.filter(g => g.querySelector('svg.tmdb-ficon')).length,
    advBgDiff: advBgDiff,
    advRadius: radius(adv),
    barRows: (() => {
      // 字段行是否被挤成两行：按字段底边去重后的行数
      const tops = new Set(qa('.tmdb-filters .tmdb-filter-group').map(
        g => Math.round(g.getBoundingClientRect().top)));
      return tops.size;
    })(),
    barH: (() => { const b = q('.tmdb-filter-bar'); return b ? Math.round(b.getBoundingClientRect().height) : null; })(),
    chipRadius: radius(chipOn),
    chipHeights: chips.map(c => Math.round(c.getBoundingClientRect().height)),
    chipOnColor: chipOn ? getComputedStyle(chipOn).color : null,
    capTitles: qa('#tmdbFilters .tmdb-fcap-title').map(e => e.textContent.trim()),
    // A7：标题行是否被底卡上边框横切（issue #24）。
    // 判据：内容盒顶边 = 底卡顶边 + 边框 + 内边距；标题/胶囊顶边必须 ≥ 该值。
    advLegend: (() => {
      const legend = q('#tmdbAdv .tmdb-adv-legend');
      const hint = q('#tmdbAdv .tmdb-adv-hint');
      if (!adv || !legend) return null;
      const aRect = adv.getBoundingClientRect();
      const aCs = getComputedStyle(adv);
      const contentTop = aRect.top + parseFloat(aCs.borderTopWidth)
                       + parseFloat(aCs.paddingTop);
      const lr = legend.getBoundingClientRect();
      const hr = hint ? hint.getBoundingClientRect() : null;
      return {
        // 正数 = 溢出到内容盒之上（会被上边框横切）
        legendOverflow: +(contentTop - lr.top).toFixed(1),
        hintOverflow: hr ? +(contentTop - hr.top).toFixed(1) : null,
        // legend 必须已不再是 <legend>（用普通 div，才回到内边距流内）
        tagName: legend.tagName.toLowerCase(),
        hasLegendTag: !!q('#tmdbAdv > legend'),
        // legend 底边不得越过 fieldset 底边（不能靠「溢出到卡片外」来规避）
        legendBottomInside: +(lr.bottom - aRect.bottom).toFixed(1),
        advH: Math.round(aRect.height),
      };
    })(),
    reset: resetR ? {w: Math.round(resetR.width), h: Math.round(resetR.height),
                     text: (reset.textContent || '').trim()} : null,
    // A8：冗余提示不得回来（issue #57 追问「这个提示有点多余了」）
    advHintCount: qa('#tmdbAdv .tmdb-adv-hint').length,
    advLegendText: (q('#tmdbAdv .tmdb-adv-legend') || {}).textContent
      ? q('#tmdbAdv .tmdb-adv-legend').textContent.replace(/\s+/g, '').trim() : '',
    advText: (adv && adv.textContent) ? adv.textContent.replace(/\s+/g, '') : '',
  };
}
"""

# 重置语义：把 9 项条件改乱，点重置，断言全部回到默认。
RESET_SEED = r"""
() => {
  document.getElementById('tmdbListType').value = 'discover';
  document.getElementById('tmdbListType').dispatchEvent(new Event('change'));
}
"""
RESET_MESS = r"""
() => {
  document.getElementById('tmdbSort').value = 'vote_average.desc';
  document.getElementById('tmdbMinRating').value = '7.5';
  document.getElementById('tmdbYear').value = '2021';
  document.getElementById('tmdbSort').dispatchEvent(new Event('change'));
  const c = document.querySelector('#tmdbGenreChips .chip[data-gid="35"]');
  if (c) c.click();
}
"""
RESET_READ = r"""
() => {
  const v = id => { const e = document.getElementById(id); return e ? e.value : null; };
  const onChips = [...document.querySelectorAll('#tmdbGenreChips .chip.on')]
    .map(c => (c.textContent || '').trim());
  const btn = document.getElementById('tmdbProviderBtn');
  return {
    mediaType: v('tmdbMediaType'), listType: v('tmdbListType'),
    region: v('tmdbRegion'), language: v('tmdbLanguage'),
    sort: v('tmdbSort'), minRating: v('tmdbMinRating'), year: v('tmdbYear'),
    providerLabel: (document.getElementById('tmdbProviderLabel') || {}).textContent || '',
    hasProviderSel: !!(btn && btn.classList.contains('has-sel')),
    onChips: onChips,
  };
}
"""
# 期望的「重置后」状态：分类=电影、榜单=首个、地区/语言=首个、
# 排序=首个、评分=0、年份=空、平台=全部、类型=全部。
RESET_EXPECT = {
    "mediaType": "movie", "listType": "trending", "region": "US", "language": "zh-CN",
    "sort": "popularity.desc", "minRating": "0", "year": "",
    "providerLabel": "全部平台", "hasSelected": False,
}


def _start_server():
    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(_QuietHandler, directory=os.path.join(ROOT, "static"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


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
                page.wait_for_timeout(1000)
                page.evaluate("()=>{ try{ jumpToDiscovery(); }catch(e){} }")
                page.wait_for_timeout(900)
                m = page.evaluate(MEASURE)
                m["label"] = label
                m["width"] = width
                m["height"] = height
                report.append(m)

                def fail(msg):
                    problems.append("[{}] {}".format(label, msg))

                if not m["selectCount"]:
                    fail("发现区没有渲染出筛选控件（.tmdb-filters .tmdb-select）")
                    page.close()
                    continue

                # ---- A 视觉层次 ----
                if m["selectRadius"] < MIN_SELECT_RADIUS:
                    fail("筛选控件圆角 {}px < {}px（旧版 4px 观感像表格格子）".format(
                        m["selectRadius"], MIN_SELECT_RADIUS))
                if m["bgDiff"] is None or m["bgDiff"] < MIN_BG_DIFF:
                    fail("筛选控件与面板底色几乎同色（通道均差 {}，下限 {}）"
                         "—— 控件融进面板正是「太素」的根因".format(m["bgDiff"], MIN_BG_DIFF))
                if m["groupIcons"] != m["groupCount"]:
                    fail("有筛选字段没有图标：{}/{} 个字段带图标".format(
                        m["groupIcons"], m["groupCount"]))
                if m["advBgDiff"] is None or m["advBgDiff"] < MIN_ADV_BG_DIFF:
                    fail("高级筛选没有独立内层底卡（与面板底色通道均差 {}，下限 {}）".format(
                        m["advBgDiff"], MIN_ADV_BG_DIFF))
                if m["chipRadius"] < MIN_CHIP_RADIUS:
                    fail("类型 chips 未胶囊化（圆角 {}px，下限 {}px）".format(
                        m["chipRadius"], MIN_CHIP_RADIUS))
                if m["chipOnColor"]:
                    rgb = [int(x) for x in re.findall(r"\d+", m["chipOnColor"])[:3]] or [0, 0, 0]
                    if not (rgb[2] > rgb[0] + 60 and rgb[2] > 100):
                        fail("类型 chips 选中态不是强调色（实测 color={}）"
                             "—— 旧版用的是绿色，与页面主色不是一套".format(m["chipOnColor"]))
                if m["chipHeights"] and width <= 480 and min(m["chipHeights"]) < MIN_CHIP_TOUCH:
                    fail("移动端 chips 过矮（最小 {}px < {}px，不好点）".format(
                        min(m["chipHeights"]), MIN_CHIP_TOUCH))
                # ---- A7 标题行不得被底卡上边框横切（issue #24） ----
                lg = m.get("advLegend")
                if not lg:
                    fail("高级筛选标题行（#tmdbAdv .tmdb-adv-legend）未渲染")
                else:
                    if lg["hasLegendTag"] or lg["tagName"] == "legend":
                        fail("高级筛选标题行仍是 <legend>：UA 样式会把它提到 fieldset "
                             "边框上排版，标题会被面板上边框横切（issue #24）")
                    if lg["legendOverflow"] > MAX_LEGEND_OVERFLOW:
                        fail("「高级筛选」标题行溢出底卡内容盒 {:.1f}px（上限 {:.1f}px）"
                             "—— 标题被内层底卡上边框横切，即「字体重叠」".format(
                                 lg["legendOverflow"], MAX_LEGEND_OVERFLOW))
                    # 注：hintOverflow 探针保留，但提示已被移除（A8），
                    # 正常情况下恒为 None；一旦有人把提示加回来就会重新有值。
                    if lg["hintOverflow"] is not None:
                        fail("高级筛选标题行又出现了附属提示胶囊（hintOverflow={}）："
                             "该提示已按 issue #57 移除".format(lg["hintOverflow"]))
                    if lg["legendBottomInside"] > 0:
                        fail("「高级筛选」标题行底边超出底卡 {:.1f}px（标题应整行落在卡内）"
                             .format(lg["legendBottomInside"]))

                # ---- A8 冗余提示不得回来（issue #57 追问） ----
                if m.get("advHintCount"):
                    fail("「仅「发现」榜单有效」提示仍在（.tmdb-adv-hint 命中 {} 个）："
                         "用户已明确说这条提示多余，高级筛选本身就只作用于本模块榜单"
                         .format(m["advHintCount"]))
                for _t in (m.get("advLegendText") or "", m.get("advText") or ""):
                    if "榜单有效" in _t:
                        fail("高级筛选区仍有「…榜单有效」措辞（实测文本「{}」）"
                             .format(_t[:40]))
                if m.get("advHintCount") is None:
                    fail("缺少 advHintCount 探针：无法拦住冗余提示回归")

                # ---- B 结构与命名 ----
                if m["panelTitle"] != "TMDB 发现":
                    fail("模块标题应为「TMDB 发现」，实测「{}」".format(m["panelTitle"]))
                if "快捷筛选" not in m["capTitles"]:
                    fail("筛选栏缺少「快捷筛选」分组标题栏")
                if not m["reset"] or m["reset"]["w"] < MIN_RESET_W:
                    fail("缺少可用的「重置筛选」按钮：{}".format(m["reset"]))

                # ---- C 副作用 ----
                if m["panelAlpha"] > MAX_PANEL_ALPHA:
                    fail("发现区面板底色不再半透明（alpha={} > {}）"
                         "—— 视觉升级不得靠实底遮罩，会毁掉磨砂观感".format(
                             m["panelAlpha"], MAX_PANEL_ALPHA))

                # ---- C2 重置语义（只在 1440×900 上跑一次，避免每视口重复请求） ----
                # 注意：必须先确认按钮**可交互**再点。旧版产物（无重置按钮）会让
                # page.click 等满 30s 超时并抛异常，把「缺按钮」这条清晰的结论
                # 变成一个看不懂的 TimeoutError。
                if width == 1440 and m["reset"] and m["reset"]["w"] >= MIN_RESET_W:
                    page.evaluate(RESET_SEED)
                    page.wait_for_timeout(700)
                    page.evaluate(RESET_MESS)
                    page.wait_for_timeout(1000)
                    messed = page.evaluate(RESET_READ)
                    # 前置条件：改乱步骤必须真的生效（「重置成功」不能建立在
                    # 「其实压根没改」之上 —— 否则门禁会永远通过）
                    if messed["listType"] != "discover" or messed["sort"] != "vote_average.desc":
                        fail("前置条件不成立：改乱筛选条件的步骤没有生效（{}）".format(messed))
                    page.click("#tmdbResetBtn")
                    page.wait_for_timeout(1800)
                    after = page.evaluate(RESET_READ)
                    checks = [
                        ("分类", after["mediaType"], RESET_EXPECT["mediaType"]),
                        ("榜单类型", after["listType"], RESET_EXPECT["listType"]),
                        ("排序", after["sort"], RESET_EXPECT["sort"]),
                        ("最低评分", after["minRating"], RESET_EXPECT["minRating"]),
                        ("年份", after["year"], RESET_EXPECT["year"]),
                        ("流媒体平台", after["providerLabel"], RESET_EXPECT["providerLabel"]),
                    ]
                    for field, got, want in checks:
                        if got != want:
                            fail("「重置筛选」未还原 {}：实测 {}，应为 {}".format(field, got, want))
                    if after["onChips"] != ["全部"]:
                        fail("「重置筛选」未还原类型选中态：chips.on = {}".format(after["onChips"]))
                    if after["hasProviderSel"]:
                        fail("「重置筛选」未清除流媒体平台的 .has-sel 高亮（只改了值没改样式）")
                page.close()
            browser.close()
    finally:
        if httpd is not None:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report}, ensure_ascii=False, indent=2))
    else:
        for m in report:
            print("{:<26} 标题=「{}」 控件 {:<2}个 底色差={:<4} 圆角={:<5} 图标={}/{} 底卡差={:<4} chip圆角={:<6} 重置={}".format(
                m["label"], m["panelTitle"], m["selectCount"], str(m["bgDiff"]),
                str(m["selectRadius"]), m["groupIcons"], m["groupCount"], str(m["advBgDiff"]),
                str(m["chipRadius"]), "有" if m["reset"] else "无"))
            lg = m.get("advLegend") or {}
            print("      A7 标题行溢出 高级筛选={}px 胶囊={}px 标签={}".format(
                lg.get("legendOverflow"), lg.get("hintOverflow"), lg.get("tagName")))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p_ in problems:
                print("  - " + p_)
            return 1
        print("TMDB 发现区筛选栏 DOM 走查通过（{} 组视口，0 处问题）".format(len(report)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
