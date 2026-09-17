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
     相距过远，读起来累）。

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
        },
        "schedule": {
            "_next_runs": {},
            "savepaths": {
                "category_base": "/影视",
                "search": "/批量转存/手动搜索存",
                "tmdb": "/批量转存/TMDB",
            },
        },
    },
    "/api/dashboard/all": {
        "stats": {"today_count": 0, "week_ok": 0, "week_fail": 0, "week_total": 0,
                  "daily": []},
        "schedule_status": {}, "version": "1.1.0",
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

MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const tab = q('#tabSettings');
  if (!tab) return {missing: true};
  const content = q('.content');
  // 卡片底部纯空白 = 卡片底边 - 卡片内「最后一个可见内容」的底边。
  // 只取有文本、或是输入控件/密码眼睛按钮的元素，避免把布局占位算成内容。
  const blankOf = (card) => {
    const r = card.getBoundingClientRect();
    let bottom = r.top;
    card.querySelectorAll('*').forEach(e => {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      const isInput = e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' || e.tagName === 'BUTTON';
      if (!e.textContent.trim() && !isInput) return;
      const b = e.getBoundingClientRect().bottom;
      if (b > bottom) bottom = b;
    });
    return Math.round(r.bottom - bottom);
  };
  const cards = [...document.querySelectorAll('#tabSettings .settings-grid > .card')]
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
  const groups = [...tab.querySelectorAll('.settings-group')].map(g => {
    const r = g.getBoundingClientRect();
    return {id: g.id, w: Math.round(r.width), h: Math.round(r.height),
      x: Math.round(r.left), y: Math.round(r.top)};
  });
  return {
    cards,
    groups,
    tabH: Math.round(tab.getBoundingClientRect().height),
    viewportH: window.innerHeight,
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    contentOverflowX: content ? content.scrollWidth - content.clientWidth : 0,
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

                if m["docOverflowX"] > 0:
                    fail("文档横向溢出 {}px".format(m["docOverflowX"]))
                if m["contentOverflowX"] > 0:
                    fail("内容区横向溢出 {}px".format(m["contentOverflowX"]))

                worst = max(m["cards"], key=lambda c: c["blank"])
                if worst["blank"] > MAX_CARD_BLANK:
                    fail("卡片「{}」底部空白 {}px（上限 {}px）".format(
                        worst["title"].strip() or "#{}".format(worst["i"]),
                        worst["blank"], MAX_CARD_BLANK))

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
            browser.close()
    finally:
        if httpd is not None:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report},
                         ensure_ascii=False, indent=2))
    else:
        for m in report:
            blanks = ", ".join(str(c["blank"]) for c in m["cards"])
            print("{:<26} 页高 {:<6} 卡片底部空白 [{}]".format(
                m["label"], m["tabH"], blanks))
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
