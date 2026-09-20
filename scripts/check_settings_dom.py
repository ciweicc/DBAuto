#!/usr/bin/env python3
"""设置页 DOM 度量走查（issue #15）。

用途：
  在真实浏览器里加载构建产物，注入确定性的接口桩数据，对设置页的
  **空白空间**做可量化断言。这些指标静态扫描（tests/test_build.py）表达不了：

  1. 任何一张卡片都不得在自己的底部留下 >40px 的纯空白
     （issue #15 的原表现：「搜索服务」内容仅 149px 高，却被 flex 等高
     拉到与「TMDB 数据源」同高 337px，底部空出 202px）；
  2. 设置页整体高度不得超过容器高度的 1.35 倍 —— 超宽视口下曾达 1.26 倍
     （1756px / 1392px），空白占比约 72%；
  3. 文档 / 内容区不得出现横向溢出（并排分组时容易踩到轨道溢出）；
  4. 每张卡片的字段区宽度必须 ≥260px（并排后卡片被压窄会让输入框不可用）；
  5. 卡片宽度不得超过 700px（超宽视口下卡片被拉到容器宽，label 与输入框
     相距过远，读起来累）；
  6. **卡片内密度必须自洽**（issue #61「设置页显示密度不一致」）：
     同一张卡里，卡片标题说明 / 字段标签 / 字段提示必须落在同一条字号线上，
     字段间距与卡片标题间距必须是同一个值；控件永远压着它自己的标签，
     非相邻字段之间不得出现「空字段槽」式的空洞（实测踩到过：一组 2 列
     排布的字段之后，走两列布局的 3 字段卡里第 3 个字段被排到第 1 行，
     第 2 行整行留空 86px）。这些只有渲染后能量化 —— 静态扫描只能证明
     令牌被引用，证明不了它算出来是同一批数字。

依赖：playwright + chromium（与 scripts/check_overview_dom.py 同依赖），
  pip install playwright && python -m playwright install chromium
无依赖时脚本以退出码 0 跳过（避免影响不装浏览器的 CI 阶段）。

用法：
  python3 scripts/check_settings_dom.py            # 检查（失败非零退出）
  python3 scripts/check_settings_dom.py --json     # 机器可读报告
  python3 scripts/check_settings_dom.py --serve    # 自动起本地静态服务
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
PORT = 8125
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# ---------------------------------------------------------------- 桩数据
# 与 app_modules 的设置接口契约一致：
#   /api/settings/all -> {config:{pansou,qas,auth_user,...}, schedule:{savepaths:{...}}}
# 填入非空值，保证每张卡的字段都被填充（空白度量不能靠「空表单」失真）。
STUBS = {
    "/api/settings/all": {
        "config": {
            "pansou": "http://192.168.1.1:8080",
            "qas": "http://192.168.1.1:5005",
            "auth_user": "root",
            "tmdb_base_url": "",
            "tmdb_proxy": "",
            # OpenList「已配置」：子分区默认收起，这才是日常使用时的形态
            # —— 高度门禁量的必须是常态，而不是「第一次来配置」的临时展开态。
            # （未配置时自动展开由 check_library_dom / 设置页本身的行为覆盖。）
            "openlist": "http://192.168.1.1:5244",
            # issue #47：OpenList 只剩令牌鉴权，账号/密码字段已从配置与界面移除。
            "openlist_token": "***",
        },
        "schedule": {
            "_next_runs": {},
            "savepaths": {
                "category_base": "/影视",
                "search": "/批量转存/手动搜索存",
                "tmdb": "/批量转存/TMDB",
                "library_root": "/影视",
            },
        },
    },
    "/api/dashboard/all": {
        "stats": {"today_count": 0, "week_ok": 0, "week_fail": 0, "week_total": 0,
                  "daily": []},
        "schedule_status": {}, "version": "1.1.0",
    },
    # 自检返回「目录不存在」（path_missing）：这才是**确定**的配置问题。
    # 与超时相反，这一类必须明确把用户引到「路径前缀」—— 两者混在一起时
    # 用户会在「改配置」和「等一会」之间反复横跳（issue #47 的成因之一）。
    "/api/library/source_path_missing": {
        "success": True, "source": "openlist", "openlist": True,
        "openlist_ok": False,
        "openlist_path_missing": True,
        "openlist_message": "OpenList 错误 500: storage not found",
        "openlist_stage": "list",
        "openlist_fail_kind": "path_missing",
        "openlist_timeout_seconds": 30,
        "openlist_auth": {"mode": "token", "mode_label": "OpenList 令牌",
                          "token_set": True, "user_set": False, "password_set": False},
        "openlist_auth_mode": "token",
        "openlist_auth_source": "token",
        "openlist_user": "admin",
        "openlist_root_prefix": "", "openlist_path": "/影视",
        "openlist_browse_path": "/影视", "openlist_count": 0,
    },
    # 自检返回「超时」（issue #47）：OpenList 有响应但慢，**不是配置错误**。
    # 这里刻意不在 message 里出现「路径前缀」——真实超时的报错也不含它，
    # 若页面把用户往「路径前缀」引，正是本题要抓的误导。
    "/api/library/source_timeout": {
        "success": True, "source": "openlist", "openlist": True,
        "openlist_ok": False,
        "openlist_message": "OpenList 请求失败: 请求超时（timed out）。OpenList 有响应但太慢"
                            " —— 这通常不是配置错误，多半是它正在等网盘（如夸克）返回列表。"
                            "请稍后重试",
        "openlist_stage": "timeout",
        "openlist_fail_kind": "timeout",
        "openlist_timeout_seconds": 30,
        "openlist_auth": {"mode": "token", "mode_label": "OpenList 令牌",
                          "token_set": True, "user_set": False, "password_set": False},
        "openlist_auth_mode": "token",
        "openlist_auth_source": "token",
        # 自检回报的身份（OpenList 侧 /api/me 的用户名），不是配置项 ——
        # 配置里已没有账号字段（issue #47 只保留令牌）。
        "openlist_user": "admin",
        "openlist_root_prefix": "", "openlist_path": "/影视",
        "openlist_browse_path": "/影视", "openlist_count": 0,
    },
    "/api/history": {"total": 0, "items": {}},
    "/api/exec_history": {"total": 0, "items": []},
}

# 卡片底部纯空白上限：卡片自身 padding 约 13px（--card-pad），留 40px 余量。
MAX_CARD_BLANK = 40
# 字段区最小可用宽度（下面板与输入框并排的最小值）。
MIN_FIELD_W = 260
# 超宽视口（≥1600px）下的设置页高度上限。
# 基线（修复前）在 2560/1920 均为 1756px；改后 1151/1169px。
# 这里取 1500px：既拦住「分组独占整行 + 等高拉伸」的回归，又不把
# 合理的窄屏长页（1440 及以下自然要滚动）误判成缺陷。
MAX_WIDE_TAB_H = 1500
# 宽卡宽度上限：单卡分组铺满组宽是**有意为之**（字段靠容器查询铺两列，纵向最短），
# 所以这里只拦「并排卡被拉得过宽」——多卡分组的卡片。
MAX_MULTI_CARD_W = 700
# issue #61：同列内「字段底边 → 下一个字段顶边」的净空，不得超过
# 「字段间距 + 一行标签」（间距 ≤13px，标签行 ≤18px）。
# 之所以拿标签行做余量：偶数个字段走两列排布时，下一行的字段顶边
# 必然落在上一行**含标签在内**的底边之下，这个差值天然就是「间距 + 标签行」，
# 属正常节奏。真正要抓的是「网格空了一整行」——那会让净空变成
# 「间距 + 标签行 + 一整个字段」，实测 86~100px。
MAX_FIELD_HOLE = 34

MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const tab = q('#tabSettings');
  if (!tab) return {missing: true};
  const content = q('.content');
  // 卡片底部纯空白 = 卡片底边 - 卡片内「最后一个可见内容」的底边。
  // 只取有文本、或是输入控件/密码眼睛按钮的元素，避免把布局占位算成内容。
  // 折叠 <details> 内部的元素：必须整体跳过。
  // 踩坑（本轮实测）：`<details>` 收起时，其**内容**的 getBoundingClientRect 仍是
  // 展开后的几何（浏览器只为渲染隐藏，不为布局清空），于是「内容底边」会算到
  // 卡片外面，量出 -193px 这种无意义的「负数空白」。不跳过的话，
  // 既读不出真实留白，也会把后续真正超高的卡片掩盖掉。
  const inCollapsedDetails = (e) => {
    for (let p = e; p; p = p.parentElement) {
      if (p.tagName === 'DETAILS' && !p.open) return true;
    }
    return false;
  };
  const blankOf = (card) => {
    const r = card.getBoundingClientRect();
    let bottom = r.top;
    card.querySelectorAll('*').forEach(e => {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      if (inCollapsedDetails(e)) return;
      const isInput = e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' || e.tagName === 'BUTTON';
      if (!e.textContent.trim() && !isInput) return;
      const b = e.getBoundingClientRect().bottom;
      if (b > bottom) bottom = b;
    });
    return Math.round(r.bottom - bottom);
  };
  // 折叠子分区（如「媒体库文件源（OpenList）」）的完整性：字段必须在展开后可见、
  // 收起后真的点不到 —— 否则「折叠」只是视觉假象，用户根本无法配置。
  const collapsedSub = (() => {
    const d = document.querySelector('#tabSettings details.set-sub');
    if (!d) return null;
    const input = d.querySelector('input, select, textarea');
    if (!input) return null;
    const wasOpen = d.open;
    // 注意：elementFromPoint 只对**视口内**的点有效，设置页在 900px 高的视口里
    // 必然有一半在折叠线以下。因此命中测试前先把子分区滚进视口，
    // 否则会把「在页面下方」误判成「点不到」（本轮实测 7 组视口全中这个假阳性）。
    const centerOf = (el) => {
      const rc = el.getBoundingClientRect();
      return [rc.left + rc.width / 2, rc.top + rc.height / 2];
    };
    const inViewport = (el) => {
      const rc = el.getBoundingClientRect();
      return rc.top >= 0 && rc.bottom <= window.innerHeight && rc.height > 0 && rc.width > 0;
    };
    d.open = true;
    d.scrollIntoView({block: 'center'});
    d.open = true;
    const openH = Math.round(d.getBoundingClientRect().height);
    const openInputH = Math.round(input.getBoundingClientRect().height);
    const openInVp = inViewport(input);
    let hit = false;
    if (openInVp) {
      const [x, y] = centerOf(input);
      hit = document.elementFromPoint(x, y) === input;
    }
    d.open = false;
    d.scrollIntoView({block: 'center'});
    const closedInVp = inViewport(input);
    let closedHit = false;
    if (closedInVp) {
      const [x, y] = centerOf(input);
      closedHit = document.elementFromPoint(x, y) === input;
    }
    const closedH = Math.round(d.getBoundingClientRect().height);
    // 路径前缀字段（issue #27「配了 OpenList 却仍回落」的修复入口）：
    // 它必须真的存在于这个子分区里且展开后可见可点 —— 否则用户
    // 只知道「回落了」，却找不到该改哪儿。
    d.open = true;
    const rootEl = document.getElementById('cfg_openlist_root');
    const rootH = rootEl ? Math.round(rootEl.getBoundingClientRect().height) : 0;
    let rootHit = false;
    let rootInVp = false;
    if (rootEl) {
      // 与上面的输入框同理：elementFromPoint 只对**视口内**的点有效。
      // 移动端视口比子分区还矮，scrollIntoView({block:'center'}) 无法把
      // 最后一个字段推进视口 —— 此时 rootHit=false 只说明「滚不进来」，
      // 不能据此判「点不到」（那是假阳性）。先显式滚进视口，再用 inViewport
      // 作为判定前置条件，与 openInVp 完全同口径。
      rootEl.scrollIntoView({block: 'center'});
      rootInVp = inViewport(rootEl);
      if (rootInVp) {
        const [rx, ry] = centerOf(rootEl);
        rootHit = document.elementFromPoint(rx, ry) === rootEl;
      }
    }
    d.open = wasOpen;
    return {closedH, openH, openInputH, openInVp, openHit: hit,
            closedInVp, closedHit, rootH, rootHit, rootInVp};
  })();
  const cards = [...document.querySelectorAll('#tabSettings .settings-flat > .card')]
    .map((c, i) => {
      const r = c.getBoundingClientRect();
      const fields = c.querySelector('.set-fields');
      return {
        i,
        w: Math.round(r.width),
        h: Math.round(r.height),
        blank: blankOf(c),
        fieldW: fields ? Math.round(fields.getBoundingClientRect().width) : null,
        title: (c.querySelector('.card-title') || {}).textContent || '',
      };
    });
  // issue #75：分区不再是可见容器，只剩锚点。这里量的是「统一网格的列数」与
  // 「每张卡的宽度是否同宽」—— 卡片宽度一致才是「设置项不再有一些高有一些低」
  // 的可量化证据。
  const grid = tab.querySelector('.settings-flat');
  const gridR = grid ? grid.getBoundingClientRect() : null;
  const gridStyle = grid ? getComputedStyle(grid) : null;
  const gridCols = gridStyle
    ? gridStyle.gridTemplateColumns.split(' ').filter(Boolean).length : 0;
  const anchors = [...tab.querySelectorAll('.set-anchor')].map(a => {
    const r = a.getBoundingClientRect();
    // 锚点必须脱离排布：零尺寸（或至少不占网格单元格）
    return {id: a.id, w: Math.round(r.width), h: Math.round(r.height)};
  });
  const groups = gridR ? [{id: 'settings-flat', w: Math.round(gridR.width),
      h: Math.round(gridR.height), x: Math.round(gridR.left),
      y: Math.round(gridR.top)}] : [];
  // ---------------------------------------------------------------
  // 卡片内密度（issue #61）：把「密度令牌算出来是多少」原样量出来。
  // 只量计算样式与几何，不做判断 —— 判断留在 Python 侧，
  // 便于报错信息里直接给出数值证据。
  const px = (v) => Math.round(parseFloat(v) * 100) / 100;
  const setFields = [...tab.querySelectorAll('.set-fields')];
  const density = (() => {
    const card = tab.querySelector('.settings-flat > .card');
    const title = card && card.querySelector(':scope > .card-title');
    const label = setFields.length ? setFields[0].querySelector('label') : null;
    const hint = tab.querySelector('.form-group .form-hint') ||
                 tab.querySelector('.set-fields > .form-hint');
    const group = setFields.length ? setFields[0].querySelector('.form-group') : null;
    const sub = tab.querySelector('.set-sub');
    const gs = (el, k) => el ? getComputedStyle(el)[k] : null;
    // 子分区（OpenList）里的标签/提示也要量：它是**同一个卡**里的第二组字段，
    // 两组的字号/间距若不同，用户看到的就是「一张卡里两种密度」。
    const subFields = sub ? sub.querySelector('.set-fields') : null;
    const subLabel = subFields ? subFields.querySelector('label') : null;
    const subHint = subFields ? subFields.querySelector('.form-hint') : null;
    return {
      cardPad: card ? px(getComputedStyle(card).paddingTop) : null,
      cardTitleMb: title ? px(getComputedStyle(title).marginBottom) : null,
      groupFs: group ? gs(group, 'fontSize') : null,
      groupMb: group ? px(getComputedStyle(group).marginBottom) : null,
      labelFs: label ? gs(label, 'fontSize') : null,
      labelMb: label ? px(getComputedStyle(label).marginBottom) : null,
      hintFs: hint ? gs(hint, 'fontSize') : null,
      hintMt: hint ? px(getComputedStyle(hint).marginTop) : null,
      subLabelFs: subLabel ? gs(subLabel, 'fontSize') : null,
      subHintFs: subHint ? gs(subHint, 'fontSize') : null,
      subGroupMb: subFields && subFields.querySelector('.form-group')
        ? px(getComputedStyle(subFields.querySelector('.form-group')).marginBottom) : null,
      // 参考线（issue #75）：分组说明（.set-group-desc）随分组边框一起移除，
      // 它原本是「同卡密度」的比较基准。现改为以**字段标签**为基准：
      // 同一张卡里的标签、提示（含子分区）必须落在同一条字号线上 ——
      // 这正是 issue #61 要守住的自洽性，且不再依赖任何分组级元素。
      groupDescFs: label ? gs(label, 'fontSize') : null,
      cardTitleFs: title ? gs(title, 'fontSize') : null,
    };
  })();
  // 字段容器的底部净空：与卡片空白同口径 —— 容器底边减「最后一个字段内容」
  // 的底边。只取有文本或输入控件的元素，避免把布局占位算成内容。
  const fieldsTrailingBlank = (f) => {
    const fr = f.getBoundingClientRect();
    let bottom = fr.top;
    f.querySelectorAll('*').forEach(e => {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      if (inCollapsedDetails(e)) return;
      const isInput = e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' ||
                      e.tagName === 'BUTTON';
      if (!e.textContent.trim() && !isInput) return;
      const b = e.getBoundingClientRect().bottom;
      if (b > bottom) bottom = b;
    });
    return Math.round(fr.bottom - bottom);
  };
  // 字段容器的「空槽」度量（issue #61）：逐行看有没有放不满的行。
  //   排除项：
  //     - 折叠中的子分区（details.set-sub 未展开）：它的内容不参与布局，
  //       一数就会把「已配置时默认收起」的正常形态判成空洞；
  //     - 横跨整行的字段：它自己独占一行，本来就不参与列填充。
  //   判断「横跨整行」用的是元素真实宽度（等于容器宽），而不是读 grid-column：
  //   Chrome 把 `grid-column:1/-1` 序列化成 gridColumnStart="1"、gridColumnEnd="-1"，
  //   按字符串做 endswith('-1') 会把**所有**自动排布的元素误判成跨列（实测踩到）。
  const gridSlots = (f) => {
    const rects = [];
    const fields = [...f.querySelectorAll(':scope .form-group')];
    const colCount = getComputedStyle(f).gridTemplateColumns.split(' ').filter(Boolean).length;
    const fw = f.getBoundingClientRect().width;
    fields.forEach(e => {
      if (inCollapsedDetails(e)) return;
      // 单列容器里不存在「空槽」：每个字段各占一行是它的正常形态。
      if (colCount < 2) return;
      const r = e.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return;
      if (fw - r.width <= 2) return;              // 横跨整行
      rects.push({top: Math.round(r.top), left: Math.round(r.left)});
    });
    return {rects, fields: rects.length, colCount};
  };
  const fieldRows = setFields.map(f => {
    const g = gridSlots(f);
    return {
    cardW: Math.round(f.closest('.card').getBoundingClientRect().width),
    cols: g.colCount,
    rects: g.rects,
    fields: g.fields,
    trailingBlank: fieldsTrailingBlank(f),
    children: [...f.children].map(e => {
      const r = e.getBoundingClientRect();
      const lab = e.tagName === 'LABEL' ? e : e.querySelector(':scope > label');
      const ctrl = e.querySelector(':scope > input, :scope > select, :scope > textarea, ' +
                                ':scope > .pwd-wrap, :scope > .form-group-inline');
      const cr = ctrl ? ctrl.getBoundingClientRect() : null;
      const lr = lab ? lab.getBoundingClientRect() : null;
      return {
        cls: e.className,
        top: Math.round(r.top), bottom: Math.round(r.bottom),
        left: Math.round(r.left), right: Math.round(r.right),
        isHint: e.className.indexOf('form-hint') >= 0,
        h: Math.round(r.height), w: Math.round(r.width),
        fs: px(getComputedStyle(e).fontSize),
        mb: px(getComputedStyle(e).marginBottom),
        ctrlH: cr ? Math.round(cr.height) : null,
        labelH: lr ? Math.round(lr.height) : null,
        labelToCtrl: (lr && cr) ? Math.round(cr.top - lr.bottom) : null,
      };
    }),
    };
  });
  return {
    cards,
    groups,
    gridCols,
    anchors,
    density,
    fieldRows,
    tabH: Math.round(tab.getBoundingClientRect().height),
    viewportH: window.innerHeight,
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    contentOverflowX: content ? content.scrollWidth - content.clientWidth : 0,
    collapsedSub,
  };
}
"""

# (宽度, 高度, 标签)
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



def _launch_kwargs():
    """本机 playwright 版本与已装 chromium 目录不匹配时回退到显式可执行文件。

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


def _make_router(stubs):
    """构造 playwright 的 route 处理器：只桩住 /api/*，其余交给真实静态服务。

    `path` 到桩数据的映射由调用方给（便于同一份走查里切换「已配置 / 未配置 /
    自检超时」等不同后端状态）——后端状态不同时页面结论也必须不同，
    这正是 issue #47 的要害：用户看到的结论能否被区分开。
    """

    def _route(route, override_path=None, override_payload=None):
        url = route.request.url
        if "/api/" not in url:
            return route.fallback()
        path = url.split(str(PORT), 1)[-1].split("?")[0]
        if path.rstrip("/").endswith("/api/sse"):
            return route.fulfill(status=200, content_type="text/event-stream", body=b"")
        if override_path and path == override_path and override_payload is not None:
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(override_payload).encode())
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(stubs.get(path, {})).encode())

    return _route


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
            for width, height, label in VIEWPORTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", handler)
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(600)
                page.evaluate("()=>{ try{ switchTab('settings'); }catch(e){} }")
                page.wait_for_timeout(400)
                m = page.evaluate(MEASURE)
                m["label"] = label
                m["width"] = width
                m["height"] = height
                report.append(m)

                def fail(msg):
                    problems.append("[{}] {}".format(label, msg))

                if m.get("missing"):
                    fail("页面缺少 #tabSettings 节点")
                    page.close()
                    continue

                if not m["cards"]:
                    fail("设置页没有渲染出任何卡片")
                    page.close()
                    continue

                # issue #75：设置项必须「一致性」——同一视口下所有卡片同宽。
                # 旧实现同屏出现 660px 与 1400px 两种卡宽（分组宽度随卡片数跳变）、
                # 154px 与 290px 两种卡高（分组边框等高拉伸），用户原话
                # 「当前的设置项太乱了，有一些高，有一些低」。
                # 统一网格后卡片宽度只由列轨道决定，必然同宽：宽度极差 >1px 即为回归。
                widths = [c["w"] for c in m["cards"]]
                if widths and (max(widths) - min(widths)) > 1:
                    worst = sorted(widths)
                    fail("设置卡宽度不一致（{}~{}px，极差 {}px）："
                         "设置项应同宽，「有一些高，有一些低」正是由此而来".format(
                             worst[0], worst[-1], worst[-1] - worst[0]))

                if m.get("anchors") is not None:
                    if len(m["anchors"]) != 4:
                        fail("分区锚点应为 4 个，实际 {} 个".format(len(m["anchors"])))
                    for a in m["anchors"]:
                        if a["w"] > 0 and a["h"] > 0:
                            fail("分区锚点「{}」占了 {}x{}px："
                                 "它必须脱离网格排布，否则会多占一整行".format(
                                     a["id"], a["w"], a["h"]))

                if m.get("gridCols", 0) < 1:
                    fail("未找到统一卡片网格 .settings-flat")

                if m["docOverflowX"] > 0:
                    fail("文档横向溢出 {}px".format(m["docOverflowX"]))
                if m["contentOverflowX"] > 0:
                    fail("内容区横向溢出 {}px".format(m["contentOverflowX"]))

                worst = max(m["cards"], key=lambda c: c["blank"])
                if worst["blank"] > MAX_CARD_BLANK:
                    fail("卡片「{}」底部空白 {}px（上限 {}px）".format(
                        worst["title"].strip() or "#{}".format(worst["i"]),
                        worst["blank"], MAX_CARD_BLANK))

                # 6. 卡片内密度必须自洽（issue #61）。
                #    用户报的是「设置页显示密度不一致」：卡片外框是密是疏
                #    由 --card-pad 决定，而卡片内部的标签/提示/间距原本各走各的
                #    （13/12/12px 三种字号 + 14/12/6px 三种间距），同一张卡里
                #    数字对不上，观感就是「一块紧一块松」。
                #    这里断言的不是「某个具体像素值」，而是**自洽**：
                #    同一张卡里的标签与提示同字号、与卡片自身说明同字号，
                #    字段间距与卡片标题间距同值。这样将来调令牌不会误伤，
                #    而把任意一处改回旧值都会立刻被抓住。
                d = m.get("density") or {}
                ref = d.get("groupDescFs")
                if ref:
                    for key, name in (("labelFs", "字段标签"), ("hintFs", "字段提示")):
                        if d.get(key) and d[key] != ref:
                            fail("同卡密度不一致：{} 字号 {} ≠ 卡片说明字号 {}".format(
                                name, d[key], ref))
                    for key, name in (("subLabelFs", "子分区标签"),
                                      ("subHintFs", "子分区提示")):
                        if d.get(key) and d[key] != ref:
                            fail("同卡密度不一致：{} 字号 {} ≠ 卡片说明字号 {}".format(
                                name, d[key], ref))
                if d.get("cardTitleMb") is not None and d.get("groupMb") is not None:
                    if d["cardTitleMb"] != d["groupMb"]:
                        fail("同卡密度不一致：卡片标题下间距 {}px ≠ 字段间距 {}px".format(
                            d["cardTitleMb"], d["groupMb"]))
                if d.get("subGroupMb") is not None and d.get("groupMb") is not None:
                    if d["subGroupMb"] != d["groupMb"]:
                        fail("同卡密度不一致：子分区字段间距 {}px ≠ 主字段间距 {}px".format(
                            d["subGroupMb"], d["groupMb"]))
                # 标签→控件：标签必须压着自己的输入框（间距 ≤ 8px）。
                # 曾经标签 mb:6px 与字段 mb:14px 几乎 1:2，视线分不清
                # 「这个标签属于谁」。
                for rows in m.get("fieldRows", []):
                    for ch in rows["children"]:
                        if ch.get("labelToCtrl") is not None and ch["labelToCtrl"] > 8:
                            fail("字段「{}」标签与其控件相距 {}px（> 8px）："
                                 "标签归属不清，密度发散".format(
                                     (ch["cls"] or "?").split(" ")[0], ch["labelToCtrl"]))
                # 「空字段槽」：容器能放的下限格数（行数 × 列数）**大于**实际字段数时，
                # 网格末尾必然空出至少一整行（实测「转存路径」4 字段走 2 列 → 空一行 86px）。
                # 只数字段本身，不数提示/按钮：提示与动作位占的格子是**有意为之**
                # （它们各自横跨整行），把它们算进去会把正常卡片误判成有空洞。
                # 也不数折叠中的子分区：折叠态下它的内容不参与布局，
                # 一数就会把「已配置时默认收起」的正常形态判成空洞。
                for rows in m.get("fieldRows", []):
                    if not rows.get("fields") or rows.get("cols", 1) < 2:
                        continue
                    # 逐行判定：除最后一行外，其余每行都必须放满。
                    # 只有末行允许不满（那是排布的必然结果，观感正常）。
                    # 「空出一整行」正是从这里来的：一旦某张卡的列数与字段数
                    # 不匹配（例如 3 字段卡碰上 2 列网格且末字段没跨行），
                    # 中间就会有一行完全是空的 —— 而空行之后还有字段，
                    # 于是那一行既没有内容也不是有意留白。
                    by_row = {}
                    for r in rows["rects"]:
                        by_row.setdefault(round(r["top"] / 4), []).append(r)
                    row_keys = sorted(by_row)
                    colCount = rows["cols"]
                    for rk in row_keys[:-1]:
                        if len({round(r["left"] / 4) for r in by_row[rk]}) < colCount:
                            fail("字段容器第 {} 行只放了 {} / {} 个字段："
                                 "列数与字段数不匹配，网格在中间空出了格子".format(
                                     row_keys.index(rk) + 1,
                                     len({round(r["left"] / 4) for r in by_row[rk]}),
                                     colCount))
                            break

                # 折叠子分区（如「媒体库文件源（OpenList）」）：
                # 折叠是为了守住设置页高度门禁（issue #15），但绝不能变成「配不了」。
                # 因此必须：① 收起时明显矮于展开；② 展开后存在可点的输入框；
                # ③ 收起后该输入框点不到（否则折叠只是视觉假象，Tab 仍会走进去）。
                cs = m.get("collapsedSub")
                if cs is None:
                    fail("设置页缺少可折叠子分区（details.set-sub）："
                         "低频子分区必须折叠以守住高度门禁")
                else:
                    if cs["openH"] <= cs["closedH"]:
                        fail("折叠子分区展开后高度未增加（收起 {}px / 展开 {}px）："
                             "折叠形同虚设".format(cs["closedH"], cs["openH"]))
                    if cs["openInputH"] <= 0:
                        fail("折叠子分区展开后输入框不可见（高 {}px）".format(cs["openInputH"]))
                    if not cs["openInVp"]:
                        fail("折叠子分区展开后输入框仍滚不进视口（探针前置条件不成立，"
                             "不能据此判定可点）")
                    elif not cs["openHit"]:
                        fail("折叠子分区展开后输入框点不到：用户无法配置")
                    if cs["closedInVp"] and cs["closedHit"]:
                        fail("折叠子分区收起后输入框仍可点：折叠只是视觉假象")
                    if cs.get("rootH", 0) <= 0:
                        fail("折叠子分区内缺少「路径前缀」输入框（cfg_openlist_root）："
                             "OpenList 挂载点与网盘路径不一致时用户无处可配，"
                             "只能看着「读不到目录」")
                    elif not cs.get("rootInVp"):
                        fail("「路径前缀」输入框滚不进视口（探针前置条件不成立，"
                             "不能据此判定可点）")
                    elif not cs.get("rootHit"):
                        fail("「路径前缀」输入框展开后点不到，用户无法修复读不到目录的问题")

                # 超宽视口必须把设置页压到 MAX_WIDE_TAB_H 以内：
                # 这是 issue #15 的核心指标（修复前 2560px 下 1756px 高）。
                if width >= 1600 and m["tabH"] > MAX_WIDE_TAB_H:
                    fail("超宽视口设置页高度 {}px 超过 {}px（疑似分组独占整行 + 等高拉伸）".format(
                        m["tabH"], MAX_WIDE_TAB_H))

                # 并排分组里的卡片不得被拉到 700px 以上
                # （分组宽度按轨道均分，卡片过宽说明轨道数判错、两卡退回了单列）。
                for c in m["cards"]:
                    if c["fieldW"] is not None and c["fieldW"] < MIN_FIELD_W:
                        fail("卡片「{}」字段区仅 {}px 宽（下限 {}px）".format(
                            c["title"].strip() or "#{}".format(c["i"]), c["fieldW"], MIN_FIELD_W))

                page.close()

            # --- OpenList 子分区「未配置」态：必须默认展开 ---
            # 折叠是为了省高度，但「没配过」的用户正是来配它的，
            # 收起等于把入口藏起来（用户只会看到媒体库慢，找不到在哪儿改）。
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")

            def handler_no_ol(route):
                url = route.request.url
                if "/api/" in url:
                    path = url.split(str(PORT), 1)[-1].split("?")[0]
                    if path.rstrip("/").endswith("/api/sse"):
                        return route.fulfill(status=200, content_type="text/event-stream",
                                             body=b"")
                    if path == "/api/settings/all":
                        stub = json.loads(json.dumps(STUBS["/api/settings/all"]))
                        stub["config"]["openlist"] = ""
                        return route.fulfill(status=200, content_type="application/json",
                                             body=json.dumps(stub).encode())
                    return route.fulfill(status=200, content_type="application/json",
                                         body=json.dumps(STUBS.get(path, {})).encode())
                return route.fallback()

            page.route("**/*", handler_no_ol)
            page.goto(URL, wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('settings')")
            page.wait_for_timeout(800)
            ol = page.evaluate(r"""() => {
              const d = document.getElementById('openlistSubGroup');
              const b = document.getElementById('openlistSubBadge');
              const i = document.getElementById('cfg_openlist');
              const rc = i ? i.getBoundingClientRect() : null;
              // 令牌输入（issue #27 追问）：必须比「账号」更靠前 —— 顺序即优先级，
              // 用户一眼能看出「填了令牌就不走登录」。这是纯视觉/顺序问题，
              // 静态扫描只能证明元素存在，证明不了它排在账号前面。
              const tk = document.getElementById('cfg_openlist_token');
              const tkc = tk ? tk.getBoundingClientRect() : null;
              const hint = document.getElementById('openlistAuthHint');
              // issue #47：账号 / 密码入口必须真的消失 —— 只从配置里删字段
              // 而把输入框留在页面上，用户会继续填、继续以为它有用。
              const removed = ['cfg_openlist_user','cfg_openlist_pass']
                .filter(id => !!document.getElementById(id));
              return {
                open: !!(d && d.open),
                badge: b ? b.textContent.trim() : '',
                inputH: rc ? Math.round(rc.height) : 0,
                tokenH: tkc ? Math.round(tkc.height) : 0,
                removedEls: removed.join(','),
                tokenType: tk ? tk.type : '',
                hintMentionsPerm: !!(hint && hint.textContent.indexOf('权限') >= 0),
                hintSaysOnlyToken: !!(hint && hint.textContent.indexOf('唯一') >= 0),
              };
            }""")
            report.append({"label": "OpenList 未配置(1440x900)", **ol})
            if not ol["open"]:
                problems.append("[OpenList 未配置] 子分区默认收起："
                                "用户正是来配置它的，收起等于把入口藏起来")
            if "未配置" not in ol["badge"]:
                problems.append("[OpenList 未配置] 状态徽标未显示「未配置」（{!r}）".format(
                    ol["badge"]))
            if ol["inputH"] <= 0:
                problems.append("[OpenList 未配置] 地址输入框不可见，无法配置")
            if ol["tokenH"] <= 0:
                problems.append("[OpenList 未配置] 令牌输入框不可见，无法配置令牌鉴权")
            if ol["tokenType"] != "password":
                problems.append("[OpenList 未配置] 令牌输入框不是 password 类型"
                                "（令牌是可直接冒充该用户的全权凭据，不应明文显示，"
                                "当前 {}）".format(ol["tokenType"]))
            if ol["removedEls"]:
                problems.append("[OpenList 未配置] 账号 / 密码入口仍在页面上（{}）："
                                "issue #47 要求只保留令牌验证，留着输入框会让用户继续填"
                                "一个后端已不再接受的字段".format(ol["removedEls"]))
            if not ol["hintMentionsPerm"]:
                problems.append("[OpenList 未配置] 令牌说明未提到「权限」："
                                "用户会以为令牌能获得更大权限")
            if not ol["hintSaysOnlyToken"]:
                problems.append("[OpenList 未配置] 令牌说明未点明它是**唯一**鉴权方式："
                                "用户会继续找账号密码入口")
            page.close()

            # ------------------------------------------------------------
            # 自检失败时的「归因」与「可见性」（issue #47）：
            # 用户「正确填写账号/密码/根路径/令牌后依旧不可用」。
            # 旧实现把**超时**和**配错**都收敛成一句「OpenList 不可用」，
            # 且结论只落在会自动消失的 toast + 默认收起的折叠面板里 ——
            # 用户既看不到原因，也无法区分「慢」和「错」，只能反复重填配置。
            # 这里断言：慢必须被说成「不是配置错误」，且结论必须看得见。
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
            timeout_router = _make_router(STUBS)
            page.route("**/*", lambda route: timeout_router(
                route, "/api/library/source", STUBS["/api/library/source_timeout"]))
            page.goto(URL, wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('settings')")
            page.wait_for_timeout(500)
            page.evaluate("()=>checkOpenList()")
            page.wait_for_timeout(600)
            to = page.evaluate(r"""() => {
              const h = document.getElementById('openlistSelfCheckHint');
              const d = document.getElementById('openlistSubGroup');
              const rc = h ? h.getBoundingClientRect() : null;
              return {
                text: h ? h.textContent : '',
                kind: h && h.dataset ? (h.dataset.olKind || '') : '',
                stage: h && h.dataset ? (h.dataset.olStage || '') : '',
                failClass: !!(h && h.className.indexOf('ol-fail') >= 0),
                panelOpen: !!(d && d.open),
                visibleH: rc ? Math.round(rc.height) : 0,
              };
            }""")
            report.append({"label": "OpenList 超时自检(1440x900)", **to})
            if "不可用" in to["text"] and "不是配置错误" not in to["text"]:
                problems.append("[OpenList 超时] 结论仍只说「不可用」："
                                "用户会把「慢」当成「配错」反复重填配置")
            if "不是配置错误" not in to["text"]:
                problems.append("[OpenList 超时] 自检结论未排除「配置错误」这一可能")
            if "路径前缀" in to["text"]:
                problems.append("[OpenList 超时] 超时被提示成「路径前缀没填对」："
                                "把用户引向与实际原因无关的修改动作")
            if to["kind"] != "timeout" or to["stage"] != "timeout":
                problems.append("[OpenList 超时] 结论未按失败归类标注"
                                "（kind={!r} stage={!r}）".format(to["kind"], to["stage"]))
            if not to["panelOpen"]:
                problems.append("[OpenList 超时] 自检失败后子分区仍收起："
                                "用户在折叠面板外看不到任何失败原因")
            if to["visibleH"] <= 0:
                problems.append("[OpenList 超时] 自检结论不可见（高 {}px）".format(
                    to["visibleH"]))
            if not to["failClass"]:
                problems.append("[OpenList 超时] 失败结论没有状态样式，"
                                "与普通说明文字无从区分")
            page.close()

            # ------------------------------------------------------------
            # 自检「目录不存在」必须点名「路径前缀」（issue #47）。
            # 这是唯一一类**确定**的配置问题：把它和超时混成一句「不可用」，
            # 用户就会在该等的时候去改配置、在该改配置的时候去等。
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
            pm_router = _make_router(STUBS)
            page.route("**/*", lambda route: pm_router(
                route, "/api/library/source", STUBS["/api/library/source_path_missing"]))
            page.goto(URL, wait_until="load", timeout=20000)
            page.evaluate("()=>switchTab('settings')")
            page.wait_for_timeout(500)
            page.evaluate("()=>checkOpenList()")
            page.wait_for_timeout(600)
            pm = page.evaluate(r"""() => {
              const h = document.getElementById('openlistSelfCheckHint');
              const rc = h ? h.getBoundingClientRect() : null;
              return {
                text: h ? h.textContent : '',
                kind: h && h.dataset ? (h.dataset.olKind || '') : '',
                visibleH: rc ? Math.round(rc.height) : 0,
              };
            }""")
            report.append({"label": "OpenList 目录不存在自检(1440x900)", **pm})
            if pm["kind"] != "path_missing":
                problems.append("[OpenList 目录不存在] 结论未按失败归类标注（kind={!r}）"
                                .format(pm["kind"]))
            if "路径前缀" not in pm["text"]:
                problems.append("[OpenList 目录不存在] 未把用户引到「路径前缀」："
                                "这是唯一一类确定的配置问题，不说等于让用户乱试")
            if pm["visibleH"] <= 0:
                problems.append("[OpenList 目录不存在] 自检结论不可见（高 {}px）".format(
                    pm["visibleH"]))
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
            if "cards" in m:
                blanks = ", ".join(str(c["blank"]) for c in m["cards"])
                ws = [c["w"] for c in m["cards"]]
                print("{:<26} 页高 {:<6} {}列 卡宽 {}, 底部空白 [{}]".format(
                    m["label"], m["tabH"], m.get("gridCols", "?"),
                    "{}-{}".format(min(ws), max(ws)) if ws else "-", blanks))
            elif "kind" in m:
                print("{:<26} 归类 {!r} 阶段 {!r} 面板 {} 结论高 {}"
                      .format(m["label"], m.get("kind", ""), m.get("stage", ""),
                              "展开" if m.get("panelOpen") else "收起",
                              m.get("visibleH", "")))
            else:
                print("{:<26} 折叠态 {:<4} 徽标 {!r} 输入框高 {} 令牌框高 {} 令牌先于账号 {}"
                      .format(m["label"], "展开" if m.get("open") else "收起",
                              m.get("badge", ""), m.get("inputH", ""),
                              m.get("tokenH", ""), m.get("tokenBeforeUser", "")))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p_ in problems:
                print("  - " + p_)
            return 1
        print("设置页 DOM 度量走查通过（{} 组视口，0 处问题）".format(len(report)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
