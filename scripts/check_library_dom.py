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
  6. 多视口下无横向溢出、表格列不被压扁（含移动端卡片化）；
  7. **数据源徽标**（issue #27 追问）：目录由 OpenList 还是 QAS 回答必须显示出来，
     回落时明确标注 —— 否则用户无从归因「快/慢」，也看不出 OpenList 挂了；
  8. **任务对账**（issue #27 追问）：只读巡检必须能跑出「失效任务」，
     且「未核对」不能混进「失效」（这是这类功能最大的误报来源）。

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
import re
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
# 覆盖范围（issue #27 卡片视图）：
#   - 卡片视图：等高紧凑卡（**不含任何图片/海报**），文件夹卡与文件卡都要能渲染；
#   - 列表视图：保留原表格，桌面列数 / 行高断言不能因为改造被丢掉；
#   - 渲染不依赖外部服务：卡片视图不得向 TMDB 发请求，接口挂掉也照常可用。
#
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
    """两类条目都要覆盖，因为「排版很乱 / 有些有图有些没图」的成因正是
    「文件夹 / 文件 / 超长名 / 各种文件名形态」混排。

    这里刻意放进去：
      - 文件夹与文件各若干（验证类型图标正确、卡片等高）；
      - 一个超长中文文件名（验证单行省略，不再折成三行把卡片撑高）；
      - 一个很短的文件夹名；
      - 带年份/清晰度/季集标记的发布名（真实网盘文件名形态）。

    数量 12 条，足以让网格排成多行、暴露行内高度不一致。
    卡片已不含任何图片，所以这里不再需要海报桩数据。
    """
    if path in ("", "/"):
        return [
            {"fid": "d1", "name": "示例作品标题-01", "dir": True, "size": 0, "updated_at": 1758000000},
            {"fid": "d2", "name": "无法匹配的文件夹", "dir": True, "size": 0, "updated_at": 1758000001},
            {"fid": "d3", "name": "示例作品标题-04", "dir": True, "size": 0, "updated_at": 1758000002},
            {"fid": "d4", "name": "短名", "dir": True, "size": 0, "updated_at": 1758000003},
            {"fid": "f0", "name": "示例作品标题-01.2024.2160p.BluRay.x265-GROUP.mkv", "dir": False,
             "size": 8 * 1024 ** 3, "updated_at": 1758000100},
            {"fid": "f1", "name": "示例作品标题-02.mkv", "dir": False,
             "size": 1024 ** 3, "updated_at": 1758000200},
            {"fid": "f2", "name": "示例作品标题-03.2021.1080p.mkv", "dir": False,
             "size": 2 * 1024 ** 3, "updated_at": 1758000300},
            {"fid": "f3", "name": "示例作品标题-04.2019.mkv", "dir": False,
             "size": 3 * 1024 ** 3, "updated_at": 1758000400},
            {"fid": "f4", "name": "无法匹配的文件-01.mkv", "dir": False,
             "size": 512 * 1024 ** 2, "updated_at": 1758000500},
            {"fid": "f5", "name": "无法匹配的文件-02.mp4", "dir": False,
             "size": 700 * 1024 ** 2, "updated_at": 1758000600},
            {"fid": "f6", "name": "这是一个非常非常长的中文文件名用来验证单行省略是否生效.mkv", "dir": False,
             "size": 4 * 1024 ** 3, "updated_at": 1758000700},
            {"fid": "f7", "name": "示例作品标题-05.mkv", "dir": False,
             "size": 1024 ** 3, "updated_at": 1758000800},
        ]
    return [
        {"fid": "x1", "name": "示例作品标题-03.mkv", "dir": False,
         "size": 2 * 1024 ** 3, "updated_at": 1758000300},
    ]


# 说明：媒体库卡片**不再显示封面图**（issue #27 第三轮反馈：「有一些有图，
# 有一些没图，太丑了，干脆直接不要图了」）。因此这里不再需要 TMDB 海报桩，
# 也不再需要 /api/tmdb/match 的桩数据 —— 走查改为断言「页面里不存在任何图片」。
STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    # 数据源自检（issue #27 追问）：桩里 OpenList 视为已配置且可用
    "/api/library/source": {"success": True, "source": "openlist", "openlist": True,
                            "openlist_ok": True, "openlist_user": "admin",
                            "openlist_message": "",
                            # 鉴权方式（issue #27 追问）：令牌鉴权下自检仍须报「身份」。
                            "openlist_auth": {"mode": "token", "mode_label": "OpenList 令牌",
                                              "token_set": True, "user_set": False,
                                              "password_set": False},
                            "openlist_auth_mode": "token",
                            "openlist_auth_source": "token",
                            "openlist_auth_label": "OpenList 令牌"},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.2.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/tmdb/list": {"items": []},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/transfer/status": {"running": False, "progress": [], "stats": {}},
    "/api/qas/tasks": _QAS_TASKS,
}

# 任务对账桩（issue #27 追问）：刻意同时包含「失效」与「未核对」两类。
# 断言要点：两类必须分开展示 —— 把「没扫到」混成「失效」会让用户去删有效的任务配置。
_AUDIT = {
    "success": True,
    "checked": 6,
    "missing": [
        {"taskname": "已失效的作品", "savepath": "/影视/电影",
         "reason": "网盘对应目录中未找到同名条目（任务可能已失效或内容被手工删除）"},
    ],
    "unverified": [
        {"taskname": "范围外的任务", "savepath": "/别的地方/目录"},
    ],
    "orphans": [{"dir": "/影视/无人管的目录", "name": "无人管的目录"}],
    "scanned_dirs": 4,
    "truncated": False,
    "message": "",
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
  const cards = [...document.querySelectorAll('#libGrid .lib-card')];
  const titles = [...document.querySelectorAll('#libGrid .lib-card-title')];
  const mets = [...document.querySelectorAll('#libGrid .lib-card-meta')];
  const times = [...document.querySelectorAll('#libGrid .lib-card-time')];
  const ics = [...document.querySelectorAll('#libGrid .lib-card-icon')];
  const cbs = [...document.querySelectorAll('#libGrid .lib-card-cb')];
  const stat = el => {
    const b = r(el); if(!b) return null;
    return {t: Math.round(b.top), h: Math.round(b.height), w: Math.round(b.width)};
  };
  const span = els => els.length ? (Math.max(...els.map(e => r(e).height)) - Math.min(...els.map(e => r(e).height))) : 0;
  return {
    missing: !lib || !qas,
    libTop: libR ? Math.round(libR.top) : null,
    qasTop: qasR ? Math.round(qasR.top) : null,
    libCountText: q('#libCount') ? q('#libCount').textContent : '',
    qasCountText: q('#qasTaskCount') ? q('#qasTaskCount').textContent : '',
    libHtmlHead: lib ? lib.innerHTML.slice(0, 400) : '',
    hasBody: cards.length || rows.length,
    cards: cards.length,
    gridCols: cards.length ? getComputedStyle(q('#libGrid')).gridTemplateColumns.split(' ').length : 0,
    cardW: cards.length ? Math.round(r(cards[0]).width) : 0,
    // 行内整齐度：同一行（top 接近）的卡片高度差 —— 旧版高矮参差的直接度量
    rowSpread: (function(){
      const byRow = {};
      cards.forEach(c => { const k = Math.round(r(c).top / 10); (byRow[k] = byRow[k] || []).push(c); });
      let worst = 0;
      Object.values(byRow).forEach(g => {
        if(g.length < 2) return;
        const hs = g.map(c => r(c).height);
        worst = Math.max(worst, Math.max(...hs) - Math.min(...hs));
      });
      return Math.round(worst);
    })(),
    // 卡片里不得有任何图片：走查断言的核心（「有些有图有些没图」不得复发）
    imgsInList: lib ? lib.querySelectorAll('img').length : 0,
    // 类型图标（文件夹 / 文件）必须每张卡都有
    cardIcons: ics.length,
    // ---- 类型图标几何（issue #47「文件缩略图图标异常」）----
    // 用户给的是截图，所以这里必须用**渲染后的数值**判定「图标是否真的画出来了」：
    //   * 图形尺寸：0 尺寸 / 过小 = 图标没画出来或被压扁（<use> 找不到 symbol 时
    //     常见表现就是「有图标位、没图形」）；与外框脱节 = 「渲染错了」。
    //   * 外框与勾选框、删除按钮同量级 = 一行内控件比例齐整。
    iconBoxes: ics.map(e => { const b = r(e); return b ? [Math.round(b.width), Math.round(b.height)] : [0, 0]; }),
    iconSvgMissing: ics.filter(e => !e.querySelector('svg')).length,
    // 图形必须真的有绘制内容（path/circle/rect…），空 <svg> 同样是「图标异常」
    iconSvgEmpty: ics.filter(e => {
      const svg = e.querySelector('svg');
      if(!svg) return true;
      return !svg.querySelector('path, circle, rect, polygon, polyline, line, ellipse');
    }).length,
    // 图形实际尺寸：用内联属性与 computed style 双口径，避免只看声明不看结果
    iconSvgs: [...document.querySelectorAll('#libGrid .lib-card-icon svg')].map(svg => {
      const b = r(svg), st = getComputedStyle(svg);
      return {w: Math.round(b.width), h: Math.round(b.height),
              cssW: Math.round(parseFloat(st.width) || 0),
              vb: svg.getAttribute('viewBox') || ''};
    }),
    // 图标不得比同一行的勾选框小太多（否则视觉上就是「小得离谱」）
    cbW: cbs.length ? Math.round(r(cbs[0]).width) : 0,
    iconFillCount: [...document.querySelectorAll('#libGrid .lib-card-icon .lib-card-icon-fill')].length,
    iconDirFillCount: [...document.querySelectorAll('#libGrid .lib-card-dir .lib-card-icon-fill')].length,
    titleLines: (function(){
      const t = titles.filter(e => getComputedStyle(e).whiteSpace === 'nowrap');
      return t.every(e => e.scrollHeight <= r(e).height + 2 || true) ? 1 : 1;
    })(),
    titleWrapped: titles.filter(e => e.scrollHeight > r(e).height + 3).length,
    tMetaWrapped: mets.filter(e => e.scrollHeight > r(e).height + 3).length,
    // 每张卡必须仍有勾选框与删除入口（改造不得把主功能挤掉）
    cardCardCheckbox: cbs.filter(e => e.querySelector('input')).length,
    cardDelBtn: [...document.querySelectorAll('#libGrid .lib-icon-btn')].length,
    fileCards: lib ? lib.querySelectorAll('.lib-card-file').length : 0,
    dirCards: lib ? lib.querySelectorAll('.lib-card-dir').length : 0,
    dirEnterLinks: lib ? lib.querySelectorAll('.lib-card-dir a.lib-card-dirlink').length : 0,
    // 元信息必须不是空的（文件夹 = 文件夹；文件 = 大小）
    emptyMeta: mets.filter(e => !e.textContent.trim()).length,
    // ---- 修改时间（issue #27 跟进：用户要求「添加显示修改时间」）----
    // 文件卡必须各有且仅有一条修改时间；文件夹卡不显示时间（目录没有实测时间语义）。
    fileTimeRows: times.length,
    fileCardsWithTime: [...document.querySelectorAll('#libGrid .lib-card-file')]
      .filter(c => c.querySelector('.lib-card-time')).length,
    dirCardsWithTime: [...document.querySelectorAll('#libGrid .lib-card-dir')]
      .filter(c => c.querySelector('.lib-card-time')).length,
    // 只统计当前**可见**的那一种格式（完整 yyyy-mm-dd hh:mm 或紧凑 mm-dd hh:mm）
    timeTexts: times.map(e => {
      const vis = [...e.querySelectorAll('.lib-time-text')]
        .filter(x => getComputedStyle(x).display !== 'none');
      return vis.length ? vis[0].textContent.trim() : '';
    }),
    // 时间必须看起来是时间（完整格式或紧凑格式），而不是空
    timeBadFormat: times.filter(e => {
      const vis = [...e.querySelectorAll('.lib-time-text')]
        .filter(x => getComputedStyle(x).display !== 'none');
      if(!vis.length) return true;
      return !/\d{2}-\d{2} \d{2}:\d{2}/.test(vis[0].textContent) ||
             /^—/.test(vis[0].textContent.trim());
    }).length,
    // 时间**不得被省略号截断**（本轮实测的坑：卡片只有 180~240px，
    // 「修改 2026-09-17 20:04」约需 112px，直接渲染会被截成「修改 2025-0…」，
    // 用户看到的仍然是「没有修改时间」）
    timeTruncated: times.filter(e => {
      const vis = [...e.querySelectorAll('.lib-time-text')]
        .filter(x => getComputedStyle(x).display !== 'none');
      return vis.some(x => x.scrollWidth > Math.ceil(x.getBoundingClientRect().width) + 1);
    }).length,
    // 完整时间（含年份）必须可从 title 取得，窄卡下降级后也不丢信息
    timeTitleMissing: times.filter(e => !/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(e.getAttribute('title') || '')).length,
    // 时间行必须单行省略（不与卡片标题抢高度）
    timeWrapped: times.filter(e => e.scrollHeight > r(e).height + 3).length,
    // 时间行必须比名称更淡（层级：不能两段同样抢眼）
    // 层级对比：时间行 vs 文件卡标题（只取文件卡 —— 文件夹标题是链接，颜色本就是 accent，
    // 拿它比较会永远不相等，断言等于没写；本轮实测踩到过）。
    timeColor: times.length ? getComputedStyle(times[0]).color : '',
    titleColor: (function(){
      const f = q('#libGrid .lib-card-file .lib-card-title');
      return f ? getComputedStyle(f).color : '';
    })(),
    // 时间行必须带时钟图标（扫视时把「时间」和「大小」区分开）
    timeIcons: times.filter(e => e.querySelector('svg')).length,
    // 列表视图的时间列
    rowsWithTime: rows.filter(row => {
      const cells = row.querySelectorAll('.lib-sub');
      return [...cells].some(c => /\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(c.textContent));
    }).length,
    // 全选不再独占一行：必须与面包屑同处一条控制条
    selAllSameRow: (function(){
      const crumb = q('#libBreadcrumb'), all = q('#libSelAllWrap');
      if(!crumb || !all) return false;
      // 隐藏态（display:none）时 rect 为全 0，此时只断言「没有独占的占位行」：
      // 该元素必须仍与面包屑同处一条控制条（父元素一致），且自身不占高度。
      if(r(all).height === 0) return all.parentElement === crumb.parentElement;
      return Math.abs(r(crumb).top - r(all).top) < 8;
    })(),
    crumbTop: (function(){ const c = q('#libBreadcrumb'); return c ? Math.round(r(c).top) : null; })(),
    selAllTop: (function(){ const c = q('#libSelAllWrap'); return c ? Math.round(r(c).top) : null; })(),

    // 数据源徽标（issue #27 追问）：必须显示、且文案能区分 OpenList / QAS 回落
    srcBadge: q('#libSourceBadge') ? q('#libSourceBadge').textContent : '',
    srcBadgeVisible: (function(){
      const b = q('#libSourceBadge');
      if(!b) return false;
      const st = getComputedStyle(b);
      return st.display !== 'none' && r(b).height > 0 && r(b).width > 0;
    })(),
    srcBadgeHasTitle: (function(){
      const b = q('#libSourceBadge');
      return !!(b && (b.getAttribute('title') || '').length > 5);
    })(),
    tipText: q('#libTip') ? q('#libTip').textContent : '',
    viewBtn: q('#libViewBtn') ? q('#libViewBtn').textContent : '',
    loadingHint: q('#libList #libLoadingHint') ? q('#libList #libLoadingHint').textContent : '',
    retryBtn: q('#libList button') ? q('#libList button').textContent : '',
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    pageOverflowX: page ? page.scrollWidth - page.clientWidth : 0,
    rowCols: rows.length ? getComputedStyle(rows[rows.length - 1]).gridTemplateColumns : '',
    rowH: rows.length ? Math.round(r(rows[rows.length - 1]).height) : 0,
    qasRows: qas ? qas.querySelectorAll('.lib-row:not(.lib-head)').length : 0,
    qasDelBtn: qas ? qas.querySelector('.lib-ops button') ? qas.querySelector('.lib-ops button').textContent : '' : '',
    qasActionBar: q('#qasActionBar') ? getComputedStyle(q('#qasActionBar')).display : '',
    vh: window.innerHeight,
  };
}
"""

VIEWPORTS = [
    (1440, 900, "1440x900"),
    (390, 844, "390x844 移动端"),
]


def _launch_kwargs():
    """本机 playwright 的版本与已装 chromium 目录不匹配时，回退到显式可执行文件。

    仅影响开发环境的手工走查（CI 上通常直接跳过），不改变断言内容。
    """
    import glob
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
                "entries": _entries(urllib.parse.unquote(qp)),
                # 数据源标签（issue #27 追问）：前端据此显示徽标并决定删除走哪条链路
                "source": "openlist", "fallback": False}
    elif path == "/api/qas/tasks":
        requests_log.append(time.time())
        body = dict(_QAS_TASKS)
    elif path == "/api/openlist/tasks/audit":
        requests_log.append(time.time())
        body = dict(_AUDIT)
    elif path == "/api/tmdb/match":
        # 前端已不调这个接口；若又有人在卡片里加回海报匹配，这里会记录到请求日志，
        # 走查末尾的「无图」断言就会把它暴露出来。
        requests_log.append(time.time())
        body = {"success": True, "matches": {}, "truncated": 0}
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
            browser = p.chromium.launch(args=["--no-sandbox"], **_launch_kwargs())
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

                # 数据源徽标（issue #27 追问）：让「快/慢」可归因
                if not m["srcBadgeVisible"]:
                    fail("媒体库未显示数据源徽标：用户无法知道目录是 OpenList 还是 QAS 回答的"
                         "（banner={!r}）".format(m["srcBadge"]))
                if "OpenList" not in m["srcBadge"]:
                    fail("数据源徽标未反映实际数据源（{!r}）".format(m["srcBadge"]))
                if not m["srcBadgeHasTitle"]:
                    fail("数据源徽标缺少说明性 title，用户看不出徽标含义")

                # 2/6. 溢出
                if m["docOverflowX"] > 0:
                    fail("文档横向溢出 {}px".format(m["docOverflowX"]))
                if m["pageOverflowX"] > 0:
                    fail("媒体库页横向溢出 {}px".format(m["pageOverflowX"]))
                # 默认是卡片视图：必须渲染出卡片、每张卡有勾选框与删除入口
                if m["cards"] < 6:
                    fail("卡片视图未渲染出卡片（{} 张）".format(m["cards"]))
                if m["dirCards"] < 1:
                    fail("目录卡缺失（文件夹未被渲染为可点击卡片）")
                if m["cardCardCheckbox"] < m["cards"] or m["cardDelBtn"] < m["cards"]:
                    fail("卡片缺少勾选框或删除入口（cb {} / del {} / 卡片 {}）".format(
                        m["cardCardCheckbox"], m["cardDelBtn"], m["cards"]))
                # issue #27 第三轮：「有一些有图，有一些没图，太丑了，干脆直接不要图了」
                # 因此卡片里**一个 <img> 都不允许有**（这是本轮最核心的断言）
                if m["imgsInList"] > 0:
                    fail("媒体库列表里仍有 {} 张图片（用户要求不再显示封面图）".format(
                        m["imgsInList"]))
                if m["cardIcons"] != m["cards"]:
                    fail("有卡片缺少「文件夹 / 文件」类型图标（图标 {} / 卡片 {}）".format(
                        m["cardIcons"], m["cards"]))
                # issue #47「文件缩略图图标异常」：必须有图形、尺寸成比例
                if m["iconSvgMissing"] > 0:
                    fail("有 {} 张卡的图标位里没有 <svg>".format(m["iconSvgMissing"]))
                if m["iconSvgEmpty"] > 0:
                    fail("有 {} 个图标是空 <svg>（没有 path/circle 等图形 = 图标没画出来）".format(
                        m["iconSvgEmpty"]))
                bad_box = [b for b in m["iconBoxes"] if b[0] < 16 or b[1] < 16]
                if bad_box:
                    fail("图标外框过小或塌陷（{}，要求 ≥16px）：图标会看起来像没渲染".format(
                        bad_box[:3]))
                bad_svg = [v for v in m["iconSvgs"]
                           if v["w"] < 16 or v["h"] < 16 or v["cssW"] < 16]
                if bad_svg:
                    fail("图标图形实际尺寸过小（{}，要求 ≥16px）".format(bad_svg[:3]))
                # 图形视口必须与「外框 / 图形」两段尺寸自洽，否则会发虚或被裁
                bad_vb = [v for v in m["iconSvgs"] if v["vb"] not in ("0 0 20 20", "0 0 24 24")]
                if bad_vb:
                    fail("图标 viewBox 异常（{!r}）".format([v["vb"] for v in bad_vb][:3]))
                if m["iconSvgs"] and all(v["vb"] == "0 0 20 20" for v in m["iconSvgs"]):
                    # 20 视口只允许在「同一档」的尺寸上渲染：桌面 20px / 窄屏 24px。
                    # 视口与渲染尺寸脱节（如 20 视口渲成 40px）会让笔画随缩放发虚。
                    if any(v["w"] != v["h"] or v["w"] not in (20, 24) for v in m["iconSvgs"]):
                        fail("图标视口是 20x20，但渲染尺寸不是等比且不落在 20/24px 档（{}）".format(
                            m["iconSvgs"][:3]))
                if m["cbW"] and m["iconBoxes"] and m["iconBoxes"][0][0] < m["cbW"] - 6:
                    fail("图标明显小于同行的勾选框（图标 {} vs 勾选框 {}），比例不齐".format(
                        m["iconBoxes"][0], m["cbW"]))
                if m["dirCards"] and m["iconDirFillCount"] < m["dirCards"]:
                    fail("文件夹卡缺少填充层（图形只有描边，缩到 20px 不容易辨认）")
                if m["emptyMeta"] > 0:
                    fail("有 {} 张卡的元信息为空（应显示「文件夹」或「大小 · 时间」）".format(
                        m["emptyMeta"]))
                if m["dirEnterLinks"] != m["dirCards"]:
                    fail("有文件夹卡丢了「进入目录」的点击区（链接 {} / 目录卡 {}）".format(
                        m["dirEnterLinks"], m["dirCards"]))

                # issue #27 跟进：「添加显示修改时间」
                # 文件卡必须每张都显示修改时间（含时钟图标、可读格式、单行省略）
                if m["fileTimeRows"] != m["fileCards"]:
                    fail("修改时间行数 {} != 文件卡数 {}，有文件卡没显示修改时间".format(
                        m["fileTimeRows"], m["fileCards"]))
                if m["fileCardsWithTime"] != m["fileCards"]:
                    fail("有文件卡缺少修改时间（带时间的卡 {} / 文件卡 {}）".format(
                        m["fileCardsWithTime"], m["fileCards"]))
                if m["timeBadFormat"] > 0:
                    fail("有 {} 条修改时间不是 YYYY-MM-DD HH:MM 格式（{}）".format(
                        m["timeBadFormat"], m["timeTexts"][:3]))
                if m["timeWrapped"] > 0:
                    fail("有 {} 条修改时间折行（应单行省略）".format(m["timeWrapped"]))
                if m["timeTruncated"] > 0:
                    fail("有 {} 条修改时间被省略号截断（窄卡必须降级为紧凑格式，"
                         "否则用户看到的还是「没有修改时间」；示例：{}）".format(
                             m["timeTruncated"], m["timeTexts"][:3]))
                if m["timeTitleMissing"] > 0:
                    fail("有 {} 条修改时间缺少完整的 title（降级后仍应能看到年月日）".format(
                        m["timeTitleMissing"]))
                if m["timeIcons"] != m["fileTimeRows"]:
                    fail("有修改时间行缺少时钟图标（图标 {} / 时间行 {}）".format(
                        m["timeIcons"], m["fileTimeRows"]))
                # 层级：时间行必须比标题淡（否则卡片里两段同样抢眼）
                if m["timeColor"] and m["timeColor"] == m["titleColor"]:
                    fail("修改时间与标题同色（{}），层级没有拉开".format(m["timeColor"]))

                # ---- 排版整齐度（issue #27 反馈：「这个媒体库的排版太丑了，实在是很乱」）----
                # 1. 同一行内卡片高度必须一致。旧版高度由图片比例决定（有图 2:3、
                #    无图 16:9、文件夹又一种），一行高差能到上百像素。
                if m["rowSpread"] > 8:
                    fail("同一行卡片高度不一致（最大差 {}px），网格参差不齐".format(m["rowSpread"]))
                # 2. 标题与元信息必须单行省略：旧版一个长中文名折成三行把卡片撑高
                if m["titleWrapped"] > 0:
                    fail("有 {} 个标题折行（应单行省略，否则长文件名会把卡片撑高）".format(
                        m["titleWrapped"]))
                if m["tMetaWrapped"] > 0:
                    fail("有 {} 个元信息折行（应单行省略）".format(m["tMetaWrapped"]))
                # 3. 目录栏：全选不再独占一行，必须和面包屑同一行（旧版卡片底部一条空条）
                if not m["selAllSameRow"]:
                    fail("「全选」未与面包屑同一行，卡片底部又多出空占位")
                # 4. 桌面网格列数：230px 起跳 → 1440 宽主列必须 ≥4 列（避免又变成「大空框」）
                if width >= 1200 and m["gridCols"] < 4:
                    fail("桌面网格只有 {} 列（卡片过宽会出现大片空白）".format(m["gridCols"]))
                if width >= 1200 and not (160 <= m["cardW"] <= 320):
                    fail("桌面卡片宽度 {}px 不在紧凑区间 160~320px".format(m["cardW"]))
                if width < 760 and m["rowH"] != 0 and m["rowH"] < 32:
                    fail("列表行高 {}px 过小，内容被压扁".format(m["rowH"]))
                page.close()

            # --- 2.5 列表视图：切换后仍是原表格栅格，桌面列数/行高断言必须成立 ---
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
            page.route("**/*", lambda r: _route(r, []))
            page.goto(URL, wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('library')")
            page.wait_for_timeout(1200)
            page.evaluate("()=>toggleLibView()")
            page.wait_for_timeout(400)
            lv = page.evaluate(MEASURE)
            report.append({"label": "列表视图(1440x900)", "libTop": lv["libTop"], "qasTop": lv["qasTop"],
                           "hasBody": lv["hasBody"], "libCountText": lv["libCountText"]})
            lv_rows = page.evaluate("()=>document.querySelectorAll('#libList .lib-row:not(.lib-head)').length")
            if lv_rows < 3:
                fail_rows = "列表视图未渲染表格行（{} 行）".format(lv_rows)
                problems.append("[列表视图] " + fail_rows)
            lv_cols = page.evaluate(
                "()=>{var r=document.querySelectorAll('#libList .lib-row');return r.length?getComputedStyle(r[r.length-1]).gridTemplateColumns:''}")
            if lv_cols.count("px") < 4:
                problems.append("[列表视图] 桌面表格列数异常（grid-template-columns={}）".format(lv_cols))
            lv_h = page.evaluate(
                "()=>{var r=document.querySelectorAll('#libList .lib-row');return r.length?Math.round(r[r.length-1].getBoundingClientRect().height):0}")
            if lv_h < 32:
                problems.append("[列表视图] 表格行高 {}px 过小，内容被压扁".format(lv_h))
            if "卡片视图" not in lv.get("viewBtn", ""):
                problems.append("[列表视图] 切换按钮文案未反映当前视图（{!r}）".format(lv.get("viewBtn")))
            # 修改时间在列表视图里也要显示（两种视图语义必须一致，issue #27 跟进）
            if lv.get("rowsWithTime", 0) < 3:
                problems.append("[列表视图] 修改时间列缺失（只有 {} 行带时间）".format(
                    lv.get("rowsWithTime", 0)))
            lv_cells = page.evaluate(
                "()=>{var r=document.querySelectorAll('#libList .lib-row:not(.lib-head)');"
                "return r.length?r[0].textContent:''}")
            if not re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", lv_cells):
                problems.append("[列表视图] 行内没有可读的修改时间（{!r}）".format(lv_cells[:60]))
            page.close()

            # --- 3. 慢接口：必须按时快速失败 + 重试入口 ---
            log2 = []
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
            # 目录接口「永不返回」：模拟 OpenList 卡死
            def hang(route):
                """目录接口「永不响应」，模拟 OpenList 卡死（其余接口走正常桩）。

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
            # issue #57：网盘文件模块的目录数据源只剩 OpenList，
            # 加载提示**必须**指向真正在等的那一方。若仍写「正在等 QAS 响应」，
            # 用户会去查一个已经不在链路上的服务（这正是 issue #57 的复现截图）。
            if "QAS" in (early.get("loadingHint") or ""):
                problems.append("[慢接口] 加载提示仍在说「等 QAS 响应」（hint={!r}）："
                                "数据源已是 OpenList，会把人引向查不到的链路".format(
                                    early.get("loadingHint")))
            if "OpenList" not in (early.get("loadingHint") or ""):
                problems.append("[慢接口] 加载提示未点明在等的是 OpenList（hint={!r}）".format(
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

            # --- 3.5 卡片视图不发任何 TMDB 请求：不依赖外部服务，媒体库始终可用 ---
            # 背景：上一版卡片会用文件名去 TMDB 匹配海报（接口挂了就全变占位块）。
            # 用户明确「干脆直接不要图了」后，卡片渲染应当**完全不依赖 TMDB**：
            # 不给它发请求，也不因为 TMDB 挂掉而影响浏览 / 删除。
            # 注意：这里用独立的计数器只统计 TMDB 请求。
            # 踩坑：最初把 _route 的请求日志与 TMDB 计数共用一个 list，
            # 结果目录/任务请求也被算成「TMDB 请求」，误报「仍在依赖图片服务」。
            tmdb_hits = []
            other_log = []

            def no_tmdb(route):
                url = route.request.url
                if "/api/" not in url:
                    return route.fallback()
                path = url.split(str(PORT), 1)[-1].split("?", 1)[0]
                if path == "/api/tmdb/match":
                    tmdb_hits.append(time.time())
                    return route.fulfill(status=500, content_type="application/json",
                                         body=b'{"error":"tmdb down"}')
                return _route(route, other_log)

            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
            page.route("**/*", no_tmdb)
            page.goto(URL, wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('library')")
            page.wait_for_timeout(1800)
            m35 = page.evaluate(MEASURE)
            report.append({"label": "TMDB 不可用(1440x900)", "libTop": m35["libTop"],
                           "qasTop": m35["qasTop"], "hasBody": m35["hasBody"],
                           "libCountText": m35["libCountText"],
                           "tmdbRequests": len(tmdb_hits)})
            if m35["cards"] < 3:
                problems.append("[无图卡片] 卡片视图整体消失（{} 张），媒体库变成不可用".format(
                    m35["cards"]))
            if m35["imgsInList"] > 0:
                problems.append("[无图卡片] 列表里出现了 {} 张图片".format(m35["imgsInList"]))
            del_ok = page.evaluate(
                "()=>document.querySelectorAll('#libGrid .lib-icon-btn').length>0")
            if not del_ok:
                problems.append("[无图卡片] 删除入口丢失，主功能受影响")
            # 修改时间同理：必须来自目录接口数据，不能依赖任何外部服务
            if m35.get("fileTimeRows", 0) != m35.get("fileCards", -1):
                problems.append("[无图卡片] 修改时间未随文件卡渲染（时间行 {} / 文件卡 {}）".format(
                    m35.get("fileTimeRows"), m35.get("fileCards")))
            # 卡片视图不应向 TMDB 发请求：既不依赖它，也不会因它卡住而变慢
            if tmdb_hits:
                problems.append("[无图卡片] 卡片视图仍在请求 /api/tmdb/match（{} 次），"
                                "又回到了「依赖外部图片服务」的老路".format(len(tmdb_hits)))
            page.close()

            # --- 3.7 任务对账（issue #27 追问：管理 QAS 任务条目的诉求） ---
            # 巡检必须：能跑出「失效」，且「未核对」不得混进「失效」。
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
            page.route("**/*", lambda r: _route(r, []))
            page.goto(URL, wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('library')")
            page.wait_for_timeout(1200)
            page.evaluate("()=>runTaskAudit()")
            page.wait_for_timeout(800)
            ad = page.evaluate(r"""() => {
              const unverifiedTitleOf = g => {
                const t = g.querySelector(".audit-group-title");
                if(!t) return "";
                let out = "";
                t.childNodes.forEach(n => { if(n.nodeType === 3) out += n.textContent; });
                return out.trim();
              };
              const box = document.getElementById('auditResult');
              const groups = [...box.querySelectorAll('.audit-group')];
              const titleOf = g => (g.querySelector('.audit-group-title')||{}).textContent || '';
              const bad = groups.find(g => g.classList.contains('audit-bad'));
              const texts = groups.map(titleOf);
              const missingGroup = groups.find(g => titleOf(g).includes('失效'));
              const unverifiedGroup = groups.find(g => titleOf(g).includes('未核对'));
              return {
                text: box.textContent,
                groupTitles: texts,
                missingRows: missingGroup ? missingGroup.querySelectorAll('.lib-row:not(.lib-head)').length : 0,
                // 「未核对」组的**标题文字**不得说成「失效」。
                // 口径：只取标题行里除 .audit-group-note 之外的文本 —— note 里
                // 必须写「不代表失效」（不说清语义同样会被误读），若拿整行文本
                // 去匹配，正确实现会被判成错的（本轮实测踩到这个假阳性）。
                unverifiedTitle: unverifiedGroup ? unverifiedTitleOf(unverifiedGroup) : "",
                unverifiedSaysInvalid: unverifiedGroup
                  ? /失效/.test(unverifiedTitleOf(unverifiedGroup)) : false,
                badGroupExists: !!bad,
                orphansChips: [...box.querySelectorAll('.audit-chip')].length,
                countText: (document.getElementById('auditCount')||{}).textContent || '',
                hasRetry: !!box.querySelector('button'),
                deleteBtns: box.querySelectorAll('.btn-danger').length,
              };
            }""")
            report.append({"label": "任务对账(1440x900)", **{k: ad[k] for k in (
                "missingRows", "orphansChips", "countText")}})
            if ad["missingRows"] < 1:
                problems.append("[任务对账] 未渲染出「任务失效」行（巡检等于没做）")
            if not ad["badGroupExists"]:
                problems.append("[任务对账] 「失效」组未使用警示样式，与其它结论无法区分")
            if "未核对" not in ad["text"]:
                problems.append("[任务对账] 未展示「未核对」分类："
                                "「没扫到」会被误读成「任务失效」")
            if ad["unverifiedSaysInvalid"]:
                problems.append("[任务对账] 「未核对」组的标题说成了「失效」："
                                "混类会让用户去删掉本来有效的任务配置")
            if "不代表失效" not in ad["text"]:
                problems.append("[任务对账] 未说明「未核对 ≠ 失效」的语义")
            if "已核对" not in ad["countText"]:
                problems.append("[任务对账] 计数未说明「已核对多少条」"
                                "（{!r}）".format(ad["countText"]))
            if ad["deleteBtns"]:
                problems.append("[任务对账] 对账面板出现删除按钮："
                                "巡检必须只读（自动清理会误删有效任务）")
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
            # 缓存命中后徽标必须仍然可见且不指向别的数据源。
            # 这条拦的是「徽标只在网络响应分支里渲染」这类实现 —— 那时缓存命中
            # （最常发生的路径）就会没有徽标，用户看到「缓存」却不知道数据从哪来。
            if not detail["srcBadgeVisible"]:
                problems.append("[缓存] 命中缓存后数据源徽标消失："
                                "用户看到「缓存」却不知道数据是谁给的")
            if "OpenList" not in detail["srcBadge"]:
                problems.append("[缓存] 命中缓存后数据源徽标回退成 QAS（{!r}）："
                                "source 未随目录数据一起缓存".format(detail["srcBadge"]))
            page.close()

            # --- 6.5 读取失败必须可归因（issue #57：数据源只剩 OpenList） ---
            # 用户诉求原文：「网盘文件模块移除夸克自动转存的 API 支持只保留
            # OpenList，夸克自动转存 API 并不好用」。
            # 过去这条链路是「OpenList 失败 → 回落 QAS」，现在保留的只有
            # **失败本身**：必须（1）明确说是读取失败、（2）透出 OpenList 侧的原因、
            # （3）给出可操作的修复方向、（4）不得再出现「回落 QAS」这种
            # 已经不存在的结论（留着就是把用户引向查不到的东西）。
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")

            def route_read_fail(r):
                url = r.request.url
                if "/api/" not in url:
                    return r.fallback()
                path = url.split(str(PORT), 1)[-1].split("?", 1)[0]
                if path.rstrip("/").endswith("/api/sse"):
                    return r.fulfill(status=200, content_type="text/event-stream", body=b"")
                if path == "/api/qas/library":
                    # 502 + success=False：后端 list_library_entries 的真实失败形态
                    return r.fulfill(status=502, content_type="application/json",
                                     body=json.dumps({
                                         "success": False, "entries": [],
                                         "source": "openlist", "fallback": False,
                                         "fallback_reason": "OpenList 上找不到目录 /影视/电影",
                                         "fallback_hint": "path_prefix",
                                         "kind": "path_missing",
                                         "message": "OpenList 上找不到目录 /影视/电影",
                                     }, ensure_ascii=False).encode())
                if path == "/api/qas/tasks":
                    return r.fulfill(status=200, content_type="application/json",
                                     body=json.dumps(dict(_QAS_TASKS), ensure_ascii=False).encode())
                return r.fallback()

            page.route("**/*", route_read_fail)
            page.goto(URL, wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('library')")
            page.wait_for_timeout(1200)
            fb = page.evaluate(MEASURE)
            report.append({"label": "读取失败(1440x900)", "libTop": fb["libTop"],
                           "qasTop": fb["qasTop"], "hasBody": fb["hasBody"],
                           "libCountText": fb["libCountText"]})
            fail_text = page.evaluate(
                "()=>document.getElementById('libList').textContent")
            fail_state = page.evaluate(r"""() => {
              const b = document.getElementById('libSourceBadge');
              return {
                badgeVisible: b ? (getComputedStyle(b).display !== 'none' &&
                                   b.offsetHeight > 0) : false,
                badgeText: b ? (b.textContent || '') : '',
                badgeTitle: b ? (b.getAttribute('title') || '') : '',
              };
            }""")
            if "读取失败" not in fail_text:
                problems.append("[读取失败] 未渲染失败态（{!r}）".format(fail_text[:80]))
            if "找不到目录" not in fail_text:
                problems.append("[读取失败] 未透出 OpenList 侧的原因（{!r}）："
                                "用户只知道「失败了」，不知道该去改哪儿".format(
                                    fail_text[:120]))
            if "路径前缀" not in fail_text:
                problems.append("[读取失败] 未给出可操作的修复方向（{!r}）".format(
                    fail_text[:160]))
            # issue #57：失败态**不得**再出现「回落 QAS」的旧结论 ——
            # 那条路径已移除，留着就是把用户引向一个不存在的能力。
            if "回落" in fail_text:
                problems.append("[读取失败] 失败态仍在说「回落 QAS」（{!r}）："
                                "该路径已在 issue #57 移除".format(fail_text[:120]))
            # 失败时徽标必须撤销（隐藏 ≠ 撤销：它是一句断言）
            if fail_state["badgeVisible"]:
                problems.append("[读取失败] 失败态仍挂着数据源徽标（{!r}）："
                                "页面同时说「读取失败」和「OpenList 正常」"
                                .format(fail_state["badgeText"]))
            page.close()

            # --- 6.6 未配置 OpenList：必须直接说「去哪配」，不得静默降级 ---
            # issue #57：QAS 直连已移除，所以「没配 OpenList」不再是「慢一点」，
            # 而是「这块功能不可用」。文案必须能落地到设置项，否则用户
            # 只会看到一个没有出路的失败页。
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")

            def route_unconfigured(r):
                url = r.request.url
                if "/api/" not in url:
                    return r.fallback()
                path = url.split(str(PORT), 1)[-1].split("?", 1)[0]
                if path.rstrip("/").endswith("/api/sse"):
                    return r.fulfill(status=200, content_type="text/event-stream", body=b"")
                if path == "/api/qas/library":
                    return r.fulfill(status=502, content_type="application/json",
                                     body=json.dumps({
                                         "success": False, "entries": [],
                                         "source": "openlist", "fallback": False,
                                         "kind": "unconfigured",
                                         "fallback_hint": "openlist_unconfigured",
                                         "message": "未配置 OpenList 地址，无法浏览网盘目录"
                                                    "（请到「设置 → 数据源 → 媒体库文件源（OpenList）」填写）",
                                     }, ensure_ascii=False).encode())
                if path == "/api/qas/tasks":
                    return r.fulfill(status=200, content_type="application/json",
                                     body=json.dumps(dict(_QAS_TASKS), ensure_ascii=False).encode())
                return r.fallback()

            page.route("**/*", route_unconfigured)
            page.goto(URL, wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('library')")
            page.wait_for_timeout(1200)
            un_text = page.evaluate("()=>document.getElementById('libList').textContent")
            report.append({"label": "未配置 OpenList(1440x900)"})
            if "未配置" not in un_text:
                problems.append("[未配置] 未点明「未配置 OpenList」（{!r}）".format(
                    un_text[:120]))
            if "OpenList" not in un_text:
                problems.append("[未配置] 文案里没有出现 OpenList（{!r}）："
                                "该模块现在只由它回答".format(un_text[:120]))
            if "设置" not in un_text:
                problems.append("[未配置] 未告诉用户去哪配（{!r}）".format(un_text[:160]))
            page.close()

            # --- 7. QAS 任务删除链路（issue #27 跟进） ---
            # 用户诉求：夸克自动转存为自部署服务，任务条目也要能在 DBAuto 里删。
            log4 = []
            deleted = []
            state = {"tasks": [dict(t) for t in _QAS_TASKS["tasks"]]}

            def route_del(route):
                url = route.request.url
                if "/api/" not in url:
                    return route.fallback()
                path = url.split(str(PORT), 1)[-1].split("?", 1)[0]
                if path.rstrip("/").endswith("/api/sse"):
                    return route.fulfill(status=200, content_type="text/event-stream", body=b"")
                if path == "/api/qas/tasks":
                    return route.fulfill(status=200, content_type="application/json",
                                         body=json.dumps({"success": True, "tasks": state["tasks"]},
                                                         ensure_ascii=False).encode())
                if path == "/api/qas/delete_tasks":
                    body = json.loads(route.request.post_data or "{}")
                    keys = {(i.get("taskname"), i.get("savepath")) for i in body.get("tasks", [])}
                    deleted.extend(body.get("tasks", []))
                    state["tasks"] = [t for t in state["tasks"]
                                      if (t.get("taskname"), t.get("savepath")) not in keys]
                    return route.fulfill(status=200, content_type="application/json",
                                         body=json.dumps({"success": True, "deleted": len(keys),
                                                          "tasks": state["tasks"]},
                                                         ensure_ascii=False).encode())
                return _route(route, log4)

            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
            page.route("**/*", route_del)
            page.goto(URL, wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('library')")
            page.wait_for_timeout(1200)

            def qas_state():
                return page.evaluate(MEASURE)

            m0 = qas_state()
            report.append({"label": "任务删除(1440x900)", "qasRows": m0["qasRows"],
                           "qasDelBtn": m0["qasDelBtn"]})
            if m0["qasRows"] != 8:
                problems.append("[任务删除] QAS 任务表行数异常（{} 行）".format(m0["qasRows"]))
            if m0["qasDelBtn"] != "删除":
                problems.append("[任务删除] QAS 任务行缺少删除入口（按钮文案 {!r}）".format(
                    m0["qasDelBtn"]))

            # 单条删除 → 必须弹确认（不可逆操作）
            #
            # 关键：这里必须**真的点页面上的按钮**，不能用
            # `page.evaluate("deleteQasTask(qasKey(qasTasks[0]))")` 在 JS 里
            # 现造 key —— 那样绕过了内联 onclick 属性的拼装与 HTML 解析，
            # 正是 issue #27「删除按钮点了没反应」漏网的原因：
            # qasKey 曾用 NUL 作分隔符，内联属性里的裸 NUL 被 HTML 解析器
            # 改写成 U+FFFD，回传 key 与 qasKey() 不再相等，findQasTask 落空。
            page.locator("#qasTaskList .lib-row:not(.lib-head) .lib-ops button").first.click(
                timeout=4000)
            page.wait_for_timeout(300)
            shown = page.evaluate(
                "()=>document.getElementById('confirmOverlay').classList.contains('show')")
            desc = page.evaluate("()=>document.getElementById('confirmDesc').textContent")
            if not shown:
                # 点真实按钮却没弹确认 = 删除入口彻底失效（issue #27）。
                # 直接判失败并给出定位提示，不要继续往下点（会一直等 overlay）。
                problems.append("[任务删除] 点击行内「删除」按钮后没有弹确认框"
                                "（内联参数可能被 HTML 改写，按钮成了空操作）")
            else:
                if "网盘里已转存的文件不受影响" not in desc:
                    problems.append("[任务删除] 确认文案未说明「只删任务配置、不动网盘文件」")
            if shown:
                page.click("#confirmOk", timeout=4000)
                page.wait_for_timeout(600)
            m1 = qas_state()
            if not deleted:
                problems.append("[任务删除] 确认后未调用 /api/qas/delete_tasks")
            elif len(deleted) != 1:
                problems.append("[任务删除] 单条删除提交了 {} 条".format(len(deleted)))
            if m1["qasRows"] != 7:
                problems.append("[任务删除] 单条删除后行数未减少（{} 行）".format(m1["qasRows"]))

            # 全选 → 批量删除（走页面上真实的全选框，而非直接调函数）
            page.check("#qasSelAll")
            page.wait_for_timeout(200)
            cnt = page.evaluate("()=>document.getElementById('qasSelCount').textContent")
            if "已选 7 条" not in cnt:
                problems.append("[任务删除] 全选计数异常（{!r}）".format(cnt))
            bar = page.evaluate(
                "()=>getComputedStyle(document.getElementById('qasActionBar')).display")
            if bar == "none":
                problems.append("[任务删除] 批量操作条在勾选后仍不可见")
            page.evaluate("()=>deleteSelectedQasTasks()")
            page.wait_for_timeout(300)
            if page.evaluate("()=>document.getElementById('confirmOverlay').classList.contains('show')"):
                page.click("#confirmOk", timeout=4000)
                page.wait_for_timeout(600)
            else:
                problems.append("[任务删除] 批量删除未弹确认框")
            m2 = qas_state()
            if m2["qasRows"] != 0:
                problems.append("[任务删除] 批量删除后仍有 {} 行任务".format(m2["qasRows"]))
            empty_txt = page.evaluate("()=>document.getElementById('qasTaskList').textContent")
            if "暂无任务" not in empty_txt:
                problems.append("[任务删除] 删空后未给出空态文案")

            # 横向溢出复查（新增了一列操作）
            if m2.get("docOverflowX", 0) > 0:
                problems.append("[任务删除] 文档横向溢出 {}px".format(m2["docOverflowX"]))
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
            elif "qasRows" in m:
                print("{:<22} 任务行数 {:<3} 行内删除入口 {!r}".format(
                    m["label"], m["qasRows"], m["qasDelBtn"]))
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
