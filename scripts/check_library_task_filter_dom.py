#!/usr/bin/env python3
"""QAS 转存任务的路径筛选 DOM 走查（issue #53 追问）。

用户诉求原文：「我希望这个转存任务的管理。可以加入路径筛选功能」
（附 291 条任务的列表截图）。

这类「筛选」问题的判定只能在渲染后做 —— 静态扫描能证明「代码里有个
oninput 处理器」，但证明不了「输入之后表格真的只剩匹配行」。

更要紧的是**删除安全**：加了筛选之后，「全选」与「删除所选」的语义必须
同步收窄到「看得见的行」，否则就是「筛出 3 条、删掉 291 条」。本脚本把它
拆成可复现的断言：

  1. 筛选栏存在且在任务加载后可见；
  2. 路径包含 → 表格只剩匹配行（逐行读渲染后的 savepath 文本，不是读内部数组）；
  3. 大小写不敏感（/tv 要能筛出 /影视/TV）；
  4. 任务名筛选 → 与路径筛选是 AND 关系；
  5. 路径前缀 chips → 点击即筛选，再点取消；chip 上的条数必须与真实命中数一致；
  6. 无匹配 → 出空态 + 「清除筛选」入口，而不是空白；
  7. 「清除筛选」必须把输入框与表格一起还原；
  8. **全选只勾可见行**：筛选后 Ctrl+A（或点表头全选）不得勾上被筛掉的行；
  9. **删除确认文案写明作用域**：筛选生效时确认框必须复述筛选条件；
 10. 分页：切换每页条数后首屏行数受控，翻页不丢筛选条件；
 11. 每页 ≤ 20 行时有界首屏；表格不被压扁，无横向溢出。

依赖：playwright + chromium（与 scripts/check_library_dom.py 同依赖）。
无依赖 / 无产物时以退出码 0 跳过（与其它走查脚本一致）。

用法：
  python3 scripts/check_library_task_filter_dom.py            # 检查（失败非零退出）
  python3 scripts/check_library_task_filter_dom.py --json     # 机器可读报告
  python3 scripts/check_library_task_filter_dom.py --serve    # 自动起本地静态服务
"""
import argparse
import functools
import glob
import http.server
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "static", "index_new.html")
PORT = 8141
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# ---------------------------------------------------------------- 桩数据
# 刻意构造的形态（与 app_modules/routes_library.py 契约一致）：
#   - 三条 /影视/电视剧（找到「某一类路径」而不是「某一条」）；
#   - 两条 /影视/电影；
#   - 一条 /影视/TV —— **大写**，用来验大小写不敏感；
#   - 一条 /下载/临时 —— 与「影视」无关，用来验筛选真的排除；
#   - 同名不同路径一条 —— QAS 不保证 taskname 唯一，key 是 name+savepath；
#   - 一条 savepath 为空 —— 空路径不能因为筛选就把整表打崩。
QAS_TASKS = [
    {"taskname": "瑞克和莫蒂 第九季", "savepath": "/影视/电视剧/瑞克和莫蒂 第九季",
     "shareurl": "https://pan.quark.cn/s/a1"},
    {"taskname": "提欧奥特曼", "savepath": "/影视/电视剧/提欧奥特曼",
     "shareurl": "https://pan.quark.cn/s/a2"},
    {"taskname": "赴汤蹈火", "savepath": "/影视/电视剧/赴汤蹈火",
     "shareurl": "https://pan.quark.cn/s/a3"},
    {"taskname": "好孩子", "savepath": "/影视/电影/好孩子",
     "shareurl": "https://pan.quark.cn/s/a4"},
    {"taskname": "寒战1994", "savepath": "/影视/电影/寒战1994",
     "shareurl": "https://pan.quark.cn/s/a5"},
    {"taskname": "Some Show", "savepath": "/影视/TV/Some Show",
     "shareurl": "https://pan.quark.cn/s/a6"},
    {"taskname": "临时资源", "savepath": "/下载/临时/临时资源", "shareurl": ""},
    {"taskname": "提欧奥特曼", "savepath": "/备用/提欧奥特曼", "shareurl": ""},
    {"taskname": "缺路径任务", "savepath": "", "shareurl": ""},
]
# 再补 16 条「/影视/电视剧/批量-NN」：总数到 25 条，分页与「筛选出 19 / 25 条」
# 才真的走得到（9 条时每页 10 = 1 页，翻页断言无从触发）。
QAS_TASKS += [
    {"taskname": "批量剧集-%02d" % i, "savepath": "/影视/电视剧/批量剧集-%02d" % i,
     "shareurl": "https://pan.quark.cn/s/b%02d" % i}
    for i in range(1, 17)
]
# 「/影视/电视剧」前缀的真实命中数（chip 计数与筛选结果都拿它当基准）
EXPECT_TV = len([t for t in QAS_TASKS if "/影视/电视剧" in t["savepath"]])

_QAS = {"success": True, "tasks": QAS_TASKS}
# 删除任务请求体的落盘（issue #57：开关值必须真的随请求发出）
_last_del_bodies = []
_ENTRIES = [{"fid": "d1", "name": "示例目录", "dir": True, "size": 0,
             "updated_at": 1758000000}]

STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.2.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/tmdb/list": {"items": []},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/transfer/status": {"running": False, "progress": [], "stats": {}},
    "/api/qas/tasks": _QAS,
    "/api/library/source": {"success": True, "source": "openlist", "openlist": True,
                            "openlist_ok": True, "openlist_user": "admin"},
}


def _launch_kwargs():
    """本机 playwright 版本与已装 chromium 目录不匹配时，回退到显式可执行文件。"""
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


def _route(route):
    url = route.request.url
    if "/api/" not in url:
        return route.fallback()
    path = url.split(str(PORT), 1)[-1].split("?", 1)[0]
    # 记录删除任务的请求体（issue #57）：开关到底有没有真的发给后端，
    # 只能看「实际发出的请求」—— 读 DOM 只证明用户勾上了。
    if path == "/api/qas/delete_tasks":
        try:
            _last_del_bodies.append(json.loads(route.request.post_data or "{}"))
        except Exception:
            _last_del_bodies.append({})
    if path == "/api/qas/delete_tasks":
        # 只记录请求体，**不改动**任务列表：走查还要继续跑分页 / chips / 全选等
        # 断言，真删掉那批任务会让它们全部失效（而且走查不该有副作用）。
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps({"success": True, "deleted": 0,
                                              "tasks": QAS_TASKS},
                                             ensure_ascii=False).encode())
    if path.rstrip("/").endswith("/api/sse"):
        return route.fulfill(status=200, content_type="text/event-stream", body=b"")
    if path == "/api/qas/library":
        body = {"success": True, "path": "/", "entries": _ENTRIES,
                "source": "openlist", "fallback": False}
    else:
        body = STUBS.get(path, {})
    return route.fulfill(status=200, content_type="application/json",
                         body=json.dumps(body, ensure_ascii=False).encode())


# 断言用的度量：全部基于**渲染后的 DOM 文本/属性**，而不是内部的 qasTasks 数组。
# 读内部数组只能证明「代码以为筛过了」，证明不了「用户屏幕上只剩匹配行」。
MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const rows = [...document.querySelectorAll('#qasTaskList .lib-row:not(.lib-head)')];
  const bar = q('#qasFilterBar');
  const chips = [...document.querySelectorAll('#qasPathChips .task-chip')];
  const pathEl = q('#qasFilterPath');
  const nameEl = q('#qasFilterName');
  return {
    barDisplay: bar ? getComputedStyle(bar).display : 'none',
    // 渲染后的每一行：任务名 / 路径 / 是否勾选（都从 DOM 读）
    rows: rows.map(r => {
      const cb = r.querySelector('input[type=checkbox]');
      const subs = [...r.querySelectorAll('.lib-sub')];
      return {
        name: r.querySelector('.lib-name') ? r.querySelector('.lib-name').textContent : '',
        path: subs.length ? subs[0].textContent : '',
        checked: !!(cb && cb.checked),
      };
    }),
    count: q('#qasTaskCount') ? q('#qasTaskCount').textContent : '',
    selCount: q('#qasSelCount') ? q('#qasSelCount').textContent : '',
    delDisabled: q('#qasDelBtn') ? q('#qasDelBtn').disabled : null,
    allChecked: q('#qasSelAll') ? q('#qasSelAll').checked : null,
    allIndeterminate: q('#qasSelAll') ? q('#qasSelAll').indeterminate : null,
    pathValue: pathEl ? pathEl.value : null,
    nameValue: nameEl ? nameEl.value : null,
    clearDisabled: q('#qasFilterClear') ? q('#qasFilterClear').disabled : null,
    chips: chips.map(c => ({ text: c.textContent, on: c.classList.contains('on') })),
    // 删除范围开关（issue #57）：默认必须关，且必须长在任务操作条里
    syncTasksChecked: q('#syncQasTasksChk') ? !!q('#syncQasTasksChk').checked : null,
    syncTasksInBar: !!document.querySelector('#qasActionBar #syncQasTasksChk'),
    pagerPos: q('#qasPagerPos') ? q('#qasPagerPos').textContent : '',
    prevDisabled: q('#qasPrev') ? q('#qasPrev').disabled : null,
    nextDisabled: q('#qasNext') ? q('#qasNext').disabled : null,
    emptyHtml: (function(){
      const box = q('#qasTaskList');
      if(!box) return '';
      const e = box.querySelector('.task-filter-empty');
      return e ? e.textContent : '';
    })(),
    // 布局：横向溢出（筛选栏在窄视口最容易把卡片撑破）
    overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    barRight: bar ? Math.round(bar.getBoundingClientRect().right) : 0,
    viewport: window.innerWidth,
  };
}
"""


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

            def open_page(width=1440, height=900):
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script("try{localStorage.setItem('auth_token','t')}catch(e){}")
                page.route("**/*", lambda r: _route(r))
                page.goto(URL, wait_until="load", timeout=20000)
                page.evaluate("()=>switchTab('library')")
                page.wait_for_timeout(1200)
                return page

            page = open_page()

            def st():
                return page.evaluate(MEASURE)

            def fail(msg):
                problems.append(msg)

            def set_path(v):
                page.fill("#qasFilterPath", v)
                page.wait_for_timeout(200)

            total = len(QAS_TASKS)
            s0 = st()

            # 0. 筛选栏必须存在且可见（任务加载完才显示）
            if s0["barDisplay"] == "none":
                fail("任务加载后筛选栏仍不可见（#qasFilterBar display=none）")
            if len(s0["rows"]) != min(20, total):
                fail("未筛选时应渲染首屏 {} 行（每页 20），实际 {} 行".format(
                    min(20, total), len(s0["rows"])))
                # 后面断言不可信，直接收敛
                report.append({"stage": "initial", "rows": len(s0["rows"])})
            if "共 {} 条".format(total) not in s0["count"]:
                fail("未筛选时计数文案应为「共 %d 条」，实际 %r" % (total, s0["count"]))
            if s0["clearDisabled"] is not True:
                fail("未筛选时「清除」按钮应为禁用态")

            # 1. 路径包含：只剩匹配行（逐行读渲染后的路径文本）
            set_path("/影视/电视剧")
            s = st()
            paths = [r["path"] for r in s["rows"]]
            expect = [t["savepath"] for t in QAS_TASKS
                      if "/影视/电视剧" in t["savepath"]]
            if paths != expect:
                fail("路径筛选结果不对：期望 {} 行，实际 {}".format(expect, paths))
            if "筛选出 {} / {} 条".format(len(expect), total) not in s["count"]:
                fail("筛选后计数应同时给出「筛出 / 总数」，实际 %r" % s["count"])
            if s["clearDisabled"] is not False:
                fail("筛选生效时「清除」按钮应可用")
            report.append({"stage": "path=/影视/电视剧", "rows": len(s["rows"]),
                           "count": s["count"]})

            # 2. 大小写不敏感
            set_path("/tv")
            s = st()
            paths = [r["path"] for r in s["rows"]]
            if paths != ["/影视/TV/Some Show"]:
                fail("路径筛选应为大小写不敏感（/tv 应命中 /影视/TV），实际 {}".format(paths))

            # 3. AND 语义：路径 + 任务名
            set_path("/影视")
            page.fill("#qasFilterName", "奥特曼")
            page.wait_for_timeout(200)
            s = st()
            names = [r["name"] for r in s["rows"]]
            paths = [r["path"] for r in s["rows"]]
            # 桩数据里「提欧奥特曼」有两条：/影视/电视剧 与 /备用。
            # 路径 + 任务名必须是**交集**，所以这里只应剩 1 条 ——
            # 若实现成了并集，就会把 /备用 那条也带出来（这正是要拦的回归）。
            if len(names) != 1 or "奥特曼" not in names[0]:
                fail("任务名筛选未与路径筛选取交集（期望 1 条「提欧奥特曼」，实际 {}）".format(names))
            if any(not p.startswith("/影视") for p in paths):
                fail("AND 语义被破坏：出现了路径不匹配的行 {}".format(paths))
            report.append({"stage": "path=/影视 & name=奥特曼", "names": names})

            # ---- 3.5 删除范围开关（issue #57）：网盘侧删文件时联动删同名任务 ----
            # 用户诉求：「删除 OpenList 指定文件夹下的资源库文件时，同步删除同名
            # 自动夸克转存任务」，并要一个开关（默认关 —— 删任务不可逆）。
            # 关键不是「有没有这个开关」，而是三件事：
            #   ① 开关默认必须是关的（不勾就不许动 QAS 任务表）；
            #   ② 勾上之后，请求体里**真的带上了开关值与路径范围**（UI 勾了但没发给
            #      后端 = 假功能；不带范围 = 越界删除筛选外的任务）；
            #   ③ 关着的时候绝不能发这个字段（防止读错 DOM 默认值造成静默删任务）。
            set_path("/影视/电视剧")
            page.wait_for_timeout(150)
            page.evaluate("()=>toggleQasSelectAll(true)")
            page.wait_for_timeout(200)

            s = st()
            if s["syncTasksChecked"] is not False:
                fail("同步删除任务开关必须默认关闭（实际 checked={!r}）"
                     "—— 删任务不可逆，绝不能默认打开".format(s["syncTasksChecked"]))
            if not s["syncTasksInBar"]:
                fail("同步删除任务开关不在任务操作条里：用户勾选任务时看不到这个选项")

            # ③ 默认关：删除提交的请求体里不应出现 sync_tasks
            page.evaluate("()=>deleteSelectedQasTasks()")
            page.wait_for_timeout(350)
            shown = page.evaluate("()=>{var m=document.querySelector('#confirmOverlay');"
                                  "return !!(m && getComputedStyle(m).display !== 'none')}")
            if not shown:
                fail("删除所选任务未弹确认框（后续开关断言不可信）")
            # 必须**真的点确认**：Esc 只是关掉弹窗，请求根本不会发出，
            # 断言就成了空转（读到的永远是「没有请求体」= 假通过）。
            page.click("#confirmOk")
            page.wait_for_timeout(500)
            body0 = _last_del_bodies[-1] if _last_del_bodies else {}
            if not _last_del_bodies:
                fail("点确认后没有发出 /api/qas/delete_tasks 请求（断言会空转）")
            if body0.get("sync_tasks"):
                fail("开关默认关闭时仍提交了 sync_tasks={!r}".format(body0.get("sync_tasks")))
            if body0.get("sync_tasks_path_filter") not in (None, ""):
                fail("开关关闭时不应带上任务同步的路径范围（{!r}）".format(
                    body0.get("sync_tasks_path_filter")))
            report.append({"stage": "sync-tasks-default-off",
                           "body": {k: body0.get(k)
                                    for k in ("sync_tasks", "sync_tasks_path_filter")}})

            # ② 勾上开关：删除提交的请求体必须带上 sync_tasks=true + 当前筛选条件
            # 重新选一遍（上一步真的删掉了那批任务，勾选已被清空）
            page.evaluate("()=>toggleQasSelectAll(true)")
            page.wait_for_timeout(200)
            page.check("#syncQasTasksChk")
            page.wait_for_timeout(200)
            page.evaluate("()=>deleteSelectedQasTasks()")
            page.wait_for_timeout(350)
            page.click("#confirmOk")
            page.wait_for_timeout(500)
            body1 = _last_del_bodies[-1] if _last_del_bodies else {}
            if body1.get("sync_tasks") is not True:
                fail("勾选「同步删除同名任务」后请求体未带上 sync_tasks=true（{!r}）".format(
                    body1.get("sync_tasks")))
            if body1.get("sync_tasks_path_filter") != "/影视/电视剧":
                fail("同步删除必须带上当前路径筛选，否则会去删筛选范围外的同名任务"
                     "（{!r}）".format(body1.get("sync_tasks_path_filter")))
            report.append({"stage": "sync-tasks-on",
                           "body": {k: body1.get(k)
                                    for k in ("sync_tasks", "sync_tasks_path_filter")}})
            page.uncheck("#syncQasTasksChk")
            page.wait_for_timeout(150)
            page.evaluate("()=>clearQasFilter()")
            page.evaluate("()=>toggleQasSelectAll(false)")
            page.wait_for_timeout(250)


            # 4. chips：数量与真实命中一致，点击即筛选、再点取消
            page.evaluate("()=>clearQasFilter()")
            page.wait_for_timeout(200)
            s = st()
            chip_texts = [c["text"] for c in s["chips"]]
            tv_chip = [c for c in chip_texts if c.startswith("/影视/电视剧")]
            if not tv_chip:
                fail("路径前缀 chips 未渲染出「/影视/电视剧」（实际 {}）".format(chip_texts))
            else:
                if not tv_chip[0].endswith(str(EXPECT_TV)):
                    fail("chip 上的条数应是该前缀的真实命中数 {}，实际 {!r}".format(
                        EXPECT_TV, tv_chip[0]))
                page.click("#qasPathChips .task-chip:has-text('/影视/电视剧')")
                page.wait_for_timeout(250)
                s = st()
                if len(s["rows"]) != min(EXPECT_TV, 20):
                    fail("点击 chip 后应剩 {} 行（每页 20 上限），实际 {}".format(
                        min(EXPECT_TV, 20), len(s["rows"])))
                if not any(c["on"] for c in s["chips"]):
                    fail("chip 选中后没有选中态（看不出当前按哪个前缀筛）")
                if s["pathValue"] != "/影视/电视剧":
                    fail("点击 chip 未同步到路径输入框（value={!r}）".format(s["pathValue"]))
                page.click("#qasPathChips .task-chip.on")
                page.wait_for_timeout(250)
                s = st()
                if len(s["rows"]) != min(20, total) or s["pathValue"] != "":
                    fail("再次点击同一个 chip 应取消筛选（rows={} value={!r}）".format(
                        len(s["rows"]), s["pathValue"]))
            report.append({"stage": "chips", "chips": chip_texts})

            # 5. 无匹配 → 空态 + 清除入口（不能是白板）
            set_path("/不存在的路径")
            s = st()
            if s["rows"]:
                fail("无匹配时仍渲染了 {} 行".format(len(s["rows"])))
            if "没有匹配的任务" not in s["emptyHtml"]:
                fail("无匹配时缺空态文案（实际 {!r}）".format(s["emptyHtml"]))
            page.click("#qasTaskList .task-filter-empty button")
            page.wait_for_timeout(250)
            s = st()
            if len(s["rows"]) != min(20, total) or s["pathValue"] != "":
                fail("空态里的「清除筛选」未还原列表（rows={} value={!r}）".format(
                    len(s["rows"]), s["pathValue"]))

            # 6. 全选只勾「看得见的行」（本轮最关键的安全断言）
            set_path("/影视/电视剧")
            page.wait_for_timeout(150)
            page.evaluate("()=>toggleQasSelectAll(true)")
            page.wait_for_timeout(250)
            s = st()
            checked = [r for r in s["rows"] if r["checked"]]
            vis = len(s["rows"])
            if len(checked) != vis:
                fail("筛选后全选应勾上全部可见行，实际 {}/{}".format(
                    len(checked), vis))
            if s["delDisabled"] is not False:
                fail("筛选后全选，「删除所选」应为可用态")
            # 关键：解除筛选后，被筛掉的行**必须仍未勾选**
            set_path("")
            page.wait_for_timeout(250)
            s = st()
            # 解除筛选后 DOM 只渲染第 1 页的 20 行，所以这里能直接看到的勾选数
            # 是「19 条选中里落在当前页的那些」；关键是**不能等于整表 25**。
            # 真正的判据交给下面两条：操作条计数 = 19、且被筛掉的行没有勾。
            if any(r["checked"] for r in s["rows"] if not r["path"].startswith("/影视/电视剧")):
                fail("被筛掉的行在解除筛选后仍是勾选态 —— 全选越界了")
            if s["selCount"].split("（")[0] != "已选 {} 条".format(vis):
                fail("解除筛选后操作条计数不对（{!r}，期望已选 {} 条）".format(s["selCount"], vis))
                fail("解除筛选后操作条计数不对（{!r}）".format(s["selCount"]))
            if s["allIndeterminate"] is not True:
                fail("部分选中时表头全选框应为半选态（indeterminate），实际 {} / checked={}".format(
                    s["allIndeterminate"], s["allChecked"]))
            report.append({"stage": "select-visible", "selCount": s["selCount"]})

            # 7. 取消全选只动可见行；再全选也只动可见行
            set_path("/影视/电视剧")
            page.wait_for_timeout(150)
            page.evaluate("()=>toggleQasSelectAll(true)")
            page.wait_for_timeout(200)
            page.evaluate("()=>toggleQasSelectAll(false)")
            page.wait_for_timeout(200)
            s = st()
            if any(r["checked"] for r in s["rows"]):
                fail("可见行取消全选后仍有勾选")
            page.evaluate("()=>clearQasFilter()")
            page.wait_for_timeout(200)
            s = st()
            if any(r["checked"] for r in s["rows"]):
                fail("可见行取消全选后，被筛掉的行仍残留勾选（取消全选未收敛）")

            # 8. 删除确认文案必须写明作用域（筛选条件复述）
            set_path("/影视/电视剧")
            page.wait_for_timeout(150)
            page.evaluate("()=>toggleQasSelectAll(true)")
            page.wait_for_timeout(200)
            page.evaluate("()=>deleteSelectedQasTasks()")
            page.wait_for_timeout(350)
            # 确认框是 #confirmOverlay（见 static/src/body.html + confirm.js）
            dlg_text = page.evaluate(
                "()=>{var m=document.querySelector('#confirmOverlay');"
                "return (m && getComputedStyle(m).display !== 'none') ? m.textContent : ''}")
            if "/影视/电视剧" not in dlg_text:
                fail("删除确认框未复述筛选条件（文案：{!r}）——用户无法判断要删的是哪一批".format(
                    dlg_text[:160]))
            if "{} 条任务".format(vis) not in dlg_text:
                fail("删除确认框未写明作用条数 {}（文案：{!r}）".format(vis, dlg_text[:160]))
            # 关掉确认框，别真的删（走查只读）
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
            s = st()
            if len(s["rows"]) != vis:
                fail("Esc 关闭确认框后竟真的删除了任务（剩 {} 行）".format(len(s["rows"])))
            report.append({"stage": "confirm-scope", "dlg": dlg_text[:120]})

            # 9. 分页：每页条数受控 + 翻页保留筛选条件
            page.evaluate("()=>clearQasFilter()")
            page.wait_for_timeout(200)
            page.select_option("#qasPageSize", "10")
            page.wait_for_timeout(300)
            s = st()
            if len(s["rows"]) != min(10, total):
                fail("每页 10 条时首屏应渲染 {} 行，实际 {}".format(min(10, total), len(s["rows"])))
            if "第 1 / 3 页" not in s["pagerPos"]:
                fail("分页位置文案不对（{!r}）".format(s["pagerPos"]))
            if s["prevDisabled"] is not True or s["nextDisabled"] is not False:
                fail("第 1 页时「上一页」应禁用、「下一页」应可用（prev={} next={}）".format(
                    s["prevDisabled"], s["nextDisabled"]))
            page.click("#qasNext")
            page.wait_for_timeout(250)
            s = st()
            if "第 2 / 3 页" not in s["pagerPos"]:
                fail("翻页未生效（{!r}）".format(s["pagerPos"]))
            if len(s["rows"]) != 10:
                fail("第 2 页应渲染 10 行，实际 {}".format(len(s["rows"])))
            page.click("#qasNext")
            page.wait_for_timeout(250)
            s = st()
            if s["nextDisabled"] is not True:
                fail("末页时「下一页」应禁用（{!r}）".format(s["pagerPos"]))
            if len(s["rows"]) != total - 20:
                fail("末页应渲染 {} 行，实际 {}".format(total - 20, len(s["rows"])))
            # 翻页后筛选条件仍在（且自动回到第 1 页）
            page.fill("#qasFilterPath", "/影视")
            page.wait_for_timeout(300)
            s = st()
            if "第 1 / " not in s["pagerPos"]:
                fail("改筛选条件后应回到第 1 页（{!r}）".format(s["pagerPos"]))
            if any(not r["path"].startswith("/影视") for r in s["rows"]):
                fail("翻页后筛选条件失效")
            report.append({"stage": "pager", "pos": s["pagerPos"]})
            page.close()

            # 10. 窄视口：筛选栏不得把页面撑出横向滚动条
            for width in (390, 768):
                sp = open_page(width=width, height=844)
                sp.wait_for_timeout(300)
                s = sp.evaluate(MEASURE)
                if s["overflowX"] > 1:
                    fail("{}px 视口出现横向溢出（{}px）—— 筛选栏把页面撑破了".format(
                        width, s["overflowX"]))
                if s["barDisplay"] == "none":
                    fail("{}px 视口筛选栏不可见".format(width))
                if s["barRight"] > width + 1:
                    fail("{}px 视口筛选栏右边界 {} 越出视口".format(width, s["barRight"]))
                report.append({"stage": "viewport-{}".format(width),
                               "overflowX": s["overflowX"]})
                sp.close()

            browser.close()
    finally:
        if httpd:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report},
                         ensure_ascii=False, indent=2))
    else:
        for r in report:
            print("· " + json.dumps(r, ensure_ascii=False))
        if problems:
            print("\n发现 {} 处问题：".format(len(problems)))
            for p in problems:
                print("  ✗ " + p)
        else:
            print("\nQAS 任务路径筛选走查：0 处问题")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
