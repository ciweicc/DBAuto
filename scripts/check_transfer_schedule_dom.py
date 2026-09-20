#!/usr/bin/env python3
"""「转存」页（含定时任务管理）DOM 走查（issue #68 追问）。

背景：原「转存」页与「定时」页各有一套**逐字重复**的分类 chips 与筛选控件，
用户要在两个页面各配一遍；且「定时」页没有任何「下次什么时候跑」的信息。

本轮把两页合并为一页「转存」，并把定时任务管理作为页内一张卡片。
本脚本在真实浏览器里加载构建产物，注入确定性接口桩，断言：

  1. 导航里不再有独立的「定时」页面（侧栏 / 底栏 / 页面容器三处一起消失）；
  2. 分类 chips 与筛选控件在整个页面里**只有一份**（重复 DOM 已消除）；
  3. 定时任务管理控件齐全（启用开关 / 时间 / 下次执行 / 立即执行 / 检测目录）；
  4. 「下次执行」显示的是「时间 / 未启用 / 计算失败」三态之一，不是恒为占位符；
  5. 勾选一个榜单后，保存请求里的 tasks 同时是「手动转存」和「定时转存」的来源
     （证明两处确实共用了同一份选择）；
  6. 点「立即执行」发出的 run_now 请求**带 section**（此前概览页漏传，按钮点了没反应）；
  7. 无横向溢出、无控制台错误。

依赖：playwright + chromium；缺失时以退出码 0 跳过（不阻断 CI）。
用法：
  python3 scripts/check_transfer_schedule_dom.py --serve
  python3 scripts/check_transfer_schedule_dom.py --serve --json
"""
import argparse
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "static", "index_new.html")
PORT = 8125
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# 一个最小的分类树：一个分组、一个子分类、两个类型，足以渲染 chips。
_CATS = {
    "movie": {
        "name": "电影",
        "subs": {"热门电影": {"path": "/影视/电影/热门电影",
                          "types": ["全部", "华语"]}},
    },
    "tv": {"name": "电视剧", "subs": {}},
    "variety": {"name": "综艺", "subs": {}},
}

_SCHEDULE = {
    "transfer": {"enabled": True, "time": "02:00", "limit": 5, "tasks": [],
                 "filters": {"min_rating": 7, "sort_by": "rating",
                             "year_from": 0, "year_to": 0}},
    "expired_check": {"enabled": False, "time": "03:00", "directories": ["/影视/电视剧"]},
    "savepaths": {"category_base": "/影视"},
    "_status": {"last_transfer": "2026-09-20 10:00:00",
                "last_expired_check": "2026-09-20 03:00:00"},
    "_next_runs": {"transfer": "2026-09-21 02:00", "expired_check": None},
}

STUBS = {
    "/api/categories": _CATS,
    "/api/settings/all": {"config": {}, "schedule": _SCHEDULE},
    "/api/schedule": _SCHEDULE,
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.1.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/transfer/status": {"running": False, "logs": []},
    "/api/tmdb/options": {"regions": [], "movie_list_types": [], "tv_list_types": []},
    "/api/tmdb/list": {"items": [], "total_pages": 1},
}

MEASURE = r"""
() => {
  const ids = sel => [...document.querySelectorAll(sel)].map(e => e.id).filter(Boolean);
  const q = s => document.querySelector(s);
  const content = q('.content');
  return {
    // 1. 独立定时页 / 导航项都应消失
    hasSchedulePage: !!q('#tabSchedule'),
    scheduleNav: document.querySelectorAll('[data-tab="schedule"]').length,
    navLabels: [...document.querySelectorAll('.side-nav-item')].map(e => e.textContent.trim()),
    bottomLabels: [...document.querySelectorAll('.bottom-nav .nav-item')].map(e => e.textContent.trim()),
    transferPage: !!q('#tabManual'),
    // 2. 重复 DOM：分类容器 / 筛选控件各只应有一份
    catContainers: ids('[id^=cats],[id^=schedCats]'),
    dupIds: (() => {
      const all = [...document.querySelectorAll('[id]')].map(e => e.id);
      const seen = {}, dup = [];
      all.forEach(i => { if (seen[i]) dup.push(i); else seen[i] = 1; });
      return dup;
    })(),
    ratingInputs: ids('#filterRating,#manualFilterRating'),
    limitInputs: ids('#limit,#schedLimit'),
    chipCount: document.querySelectorAll('#tabManual .chip').length,
    // 3. 定时任务管理控件
    schedJobs: document.querySelectorAll('#tabManual .sched-job').length,
    hasSchedTitle: !!([...document.querySelectorAll('#tabManual .card-title')]
      .find(e => e.textContent.includes('定时任务管理'))),
    hasTNext: !!q('#schedTNext'), hasENext: !!q('#schedENext'),
    runNowButtons: [...document.querySelectorAll('#tabManual button')]
      .filter(b => (b.getAttribute('onclick') || '').includes('runScheduleNow'))
      .map(b => b.getAttribute('onclick')),
    // 4. 下次执行的三态文案
    tNextText: q('#schedTNext') ? q('#schedTNext').textContent.trim() : null,
    eNextText: q('#schedENext') ? q('#schedENext').textContent.trim() : null,
    lastTransfer: q('#schedLastTransfer') ? q('#schedLastTransfer').textContent.trim() : null,
    // 溢出
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    contentOverflowX: content ? content.scrollWidth - content.clientWidth : 0,
    // 5. 分类与筛选共用：值回填是否生效
    ratingVal: q('#filterRating') ? q('#filterRating').value : null,
    limitVal: q('#limit') ? q('#limit').value : null,
  };
}
"""

# 勾选第一个 chip → 触发 autoSaveSchedule → 读出发往 /api/schedule 的 tasks
_SAVE_PROBE = r"""
() => {
  const chip = document.querySelector('#tabManual .chip');
  if (!chip) return null;
  chip.click();
  return {path: chip.dataset.path, type: chip.dataset.type};
}
"""

_RUNNOW_PROBE = r"""
() => {
  const b = [...document.querySelectorAll('#tabManual button')]
    .find(x => (x.getAttribute('onclick') || '').includes('runScheduleNow'));
  if (!b) return false;
  b.click();
  return true;
}
"""


def _launch_kwargs():
    import glob
    for cand in sorted(glob.glob("/opt/ms-playwright/chromium-*/chrome-linux64/chrome")):
        if os.path.isfile(cand):
            return {"executable_path": cand}
    return {}


def _start_server():
    import http.server
    import functools

    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(_Quiet, directory=os.path.join(ROOT, "static"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


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
        print("跳过：未安装 playwright")
        return 0

    httpd = _start_server() if args.serve else None
    problems, report = [], []
    posts = []          # 记录前端发出的写请求，用于断言「选择被保存」「立即执行带 section」

    def handler(route):
        req = route.request
        url = req.url
        if "/api/" not in url:
            route.fallback()
            return
        path = url.split(str(PORT), 1)[-1].split("?")[0]
        if path.rstrip("/").endswith("/api/sse"):
            route.fulfill(status=200, content_type="text/event-stream", body=b"")
            return
        if req.method == "POST":
            try:
                posts.append({"path": path, "body": json.loads(req.post_data or "{}")})
            except ValueError:
                posts.append({"path": path, "body": {}})
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True}).encode())
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(STUBS.get(path, {})).encode())

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"], **_launch_kwargs())
            for width, height, label in [(1440, 900, "1440x900"), (390, 844, "390x844")]:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.route("**/*", handler)
                page.goto(URL, wait_until="load", timeout=20000)
                page.wait_for_timeout(400)
                # 直接调 switchTab：窄屏侧栏是抽屉态，「点击」在移动端视口下不可见
                page.evaluate("() => switchTab('manual')")
                page.wait_for_timeout(500)
                m = page.evaluate(MEASURE)
                m["label"], m["width"] = label, width
                report.append(m)

                def fail(msg):
                    problems.append("[{}] {}".format(label, msg))

                if m["hasSchedulePage"]:
                    fail("仍存在独立的 #tabSchedule 页面容器")
                if m["scheduleNav"]:
                    fail("导航里仍有 {} 个 schedule 项".format(m["scheduleNav"]))
                if "定时" in m["navLabels"] or "定时" in m["bottomLabels"]:
                    fail("导航仍含「定时」：侧栏 {} / 底栏 {}".format(m["navLabels"], m["bottomLabels"]))
                if not m["transferPage"]:
                    fail("找不到合并后的 #tabManual 页面")

                # 重复 DOM
                if m["dupIds"]:
                    fail("存在重复 id（合并未清干净）：{}".format(m["dupIds"]))
                if m["ratingInputs"] != ["filterRating"]:
                    fail("最低评分输入应只剩 #filterRating，实际 {}".format(m["ratingInputs"]))
                if m["limitInputs"] != ["limit"]:
                    fail("上限输入应只剩 #limit，实际 {}".format(m["limitInputs"]))
                if m["chipCount"] < 1:
                    fail("分类 chips 未渲染（至少应有 1 个）")

                # 定时任务管理
                if not m["hasSchedTitle"]:
                    fail("缺少「定时任务管理」卡片标题")
                if m["schedJobs"] < 2:
                    fail("定时任务管理应有两项（转存 / 失效检测），实际 {} 项".format(m["schedJobs"]))
                if not (m["hasTNext"] and m["hasENext"]):
                    fail("缺少「下次执行」节点")
                if sorted(m["runNowButtons"]) != ["runScheduleNow('expired_check')",
                                                  "runScheduleNow('transfer')"]:
                    fail("「立即执行」按钮接线异常：{}".format(m["runNowButtons"]))
                # 三态之一，且启用的那条应显示时间
                if m["tNextText"] in (None, "", "-"):
                    fail("启用的定时转存未显示下次执行时间：{}".format(m["tNextText"]))
                if m["eNextText"] not in ("未启用", "计算失败") and not m["eNextText"]:
                    fail("停用的定时失效检测未显示「未启用」：{}".format(m["eNextText"]))
                if m["eNextText"] != "未启用":
                    fail("桩数据里 expired_check 未启用，应显示「未启用」，实际 {}".format(m["eNextText"]))

                if m["docOverflowX"] > 0:
                    fail("文档横向溢出 {}px".format(m["docOverflowX"]))
                if m["contentOverflowX"] > 0:
                    fail("内容区横向溢出 {}px".format(m["contentOverflowX"]))
                if errors:
                    fail("页面 JS 错误：{}".format(errors[:3]))

                # 6. 分类选择共用：勾选 chip → 保存请求里的 tasks 应带上它
                posts.clear()
                picked = page.evaluate(_SAVE_PROBE)
                page.wait_for_timeout(900)      # autoSaveSchedule 有 600ms 防抖
                saves = [x for x in posts if x["path"] == "/api/schedule"
                         and x["body"].get("action", "save") == "save"]
                if not saves:
                    fail("勾选榜单后未触发 /api/schedule 保存（分类未与定时共用）")
                else:
                    tasks = saves[-1]["body"].get("transfer", {}).get("tasks", [])
                    keys = {(t.get("path"), t.get("type")) for t in tasks}
                    if picked and picked["path"] and (picked["path"], picked["type"]) not in keys:
                        fail("保存请求缺少刚勾选的榜单 {}（选择未共用）".format(keys))
                    m["savedTaskCount"] = len(tasks)

                # 7. 立即执行必须带 section
                posts.clear()
                page.evaluate(_RUNNOW_PROBE)
                page.wait_for_timeout(500)
                runs = [x for x in posts if x["path"] == "/api/schedule"
                        and x["body"].get("action") == "run_now"]
                if not runs:
                    fail("点「立即执行」未发出 run_now 请求")
                elif runs[-1]["body"].get("section") not in ("transfer", "expired_check"):
                    fail("run_now 未带 section（后端会 400）：{}".format(runs[-1]["body"]))

                page.close()

            browser.close()
    finally:
        if httpd:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"report": report, "problems": problems}, ensure_ascii=False, indent=2))
    else:
        for m in report:
            print("[{label}] 导航={nav} 底栏={bot} chips={c} 定时卡={j} "
                  "下次(转存)={tn} 下次(检测)={en}".format(
                      label=m["label"], nav=m["navLabels"], bot=m["bottomLabels"],
                      c=m["chipCount"], j=m["schedJobs"],
                      tn=m["tNextText"], en=m["eNextText"]))
        if problems:
            print("\n发现问题 {} 项：".format(len(problems)))
            for x in problems:
                print("  - " + x)
            return 1
        print("\nOK：转存页已合并定时任务管理，重复 DOM 已消除")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
