#!/usr/bin/env python3
"""媒体库打开速度的**真实链路**计时走查（issue #27：「每次打开都要等几秒」）。

与 `scripts/check_library_dom.py` 的分工：
  - check_library_dom.py 用**桩接口**验语义（模块顺序 / 快速失败 / 缓存 / 卡片），
    请求耗时是刻意设定的，回答不了「到底等了多久」；
  - 本脚本量的是**时间本身**：从切到媒体库页到「用户真的看见内容」，
    分段记录，并按上限断言。

分段落账（这是本轮优化的依据，也是防止回归的抓手）：

  1. `first_paint`      —— 切页后第一帧可见反馈（进度文案 + 骨架卡）出现的时间；
  2. `directory_ready`  —— 网盘目录卡片真正可见的时间（骨架被替换）；
  3. `qas_ready`        —— QAS 任务表可见的时间；
  4. `reopen_*`         —— 切走再切回来（未刷新页面）的耗时，必须复用缓存；
  5. `firstH → afterH`  —— 首帧占位高度与数据到达后的高度差（页面位移）。

假 API 的延迟由 `--delay` 显式设定（默认 0.8s ≈ QAS 实查夸克目录的典型耗时），
据此推导每段的耗时上限。桩**只在页面内替换 fetch**（不是真实网络）：

  - Playwright 的 route 回调必须同步完成，同步 sleep 会阻塞整页资源加载，
    用其它线程回调又会触发 greenlet 切换错误（本仓库已两次踩坑），
    所以耗时用页面内 setTimeout 模拟，确定且与驱动进程的线程模型无冲突；
  - **必须把所有 /api/* 都给桩**：只要有一个接口返回 401，auth.js 会把页面
    直接 `location.href='/login.html'` 踢走，后面所有度量都会落到登录页上。

用法：
  python3 scripts/check_library_speed.py                 # 默认 delay=0.8s
  python3 scripts/check_library_speed.py --delay 0.2     # 快上游
  python3 scripts/check_library_speed.py --json
无 playwright / 无产物时以退出码 0 跳过（与其它走查脚本一致）。
"""
import argparse
import functools
import http.server
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
DIST = os.path.join(STATIC, "index_new.html")
PORT = 8131
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# 桩目录的条目数：取「真实媒体库」的量级，让计时覆盖一次 24 张卡的渲染成本。
DIR_COUNT = 6
FILE_COUNT = 18


def _launch_kwargs():
    """本机 playwright 版本与已装 chromium 目录不匹配时回退到显式可执行文件。"""
    import glob
    for cand in sorted(glob.glob("/opt/ms-playwright/chromium-*/chrome-linux64/chrome")):
        if os.path.isfile(cand):
            return {"executable_path": cand}
    return {}


def _start_server():
    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(_Quiet, directory=STATIC)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# 页面内的时间线脚本：用 rAF 轮询「内容是否已可见」，
# 量的是**用户看到**的时间（含一次布局/绘制），不是 DOM 写入的时间。
STUB_JS = r"""
(function(){
  var DELAY = %(delay)d;
  var origFetch = window.fetch.bind(window);
  function mk(payload){
    return new Promise(function(resolve){
      setTimeout(function(){
        resolve(new Response(JSON.stringify(payload), {
          status: 200, headers: {'Content-Type': 'application/json'}
        }));
      }, DELAY);
    });
  }
  function entries(){
    var out = [];
    for(var i = 0; i < %(dirs)d; i++){
      out.push({fid:'d'+i, name:'示例目录-'+i, dir:true, size:0, updated_at:1758000000+i});
    }
    for(var j = 0; j < %(files)d; j++){
      out.push({fid:'f'+j, name:'示例作品标题-'+j+'.2024.2160p.mkv', dir:false,
                size:2147483648, updated_at:1758000100+j});
    }
    return out;
  }
  window.fetch = function(input, init){
    var url = String(input && input.url ? input.url : input);
    if(url.indexOf('/api/qas/library') >= 0){
      var p = '/';
      var m = url.match(/[?&]path=([^&]*)/);
      if(m) p = decodeURIComponent(m[1]);
      return mk({success:true, path:p, entries: entries()});
    }
    if(url.indexOf('/api/qas/tasks') >= 0){
      var tasks = [];
      for(var k = 0; k < 6; k++){
        tasks.push({taskname:'示例任务-'+k, savepath:'/媒体库/电影',
                    shareurl:'https://pan.quark.cn/s/demo'+k});
      }
      return mk({success:true, tasks: tasks});
    }
    // 其余接口：立即返回空响应。**不能省**——任何 401 都会把页面踢到登录页。
    if(url.indexOf('/api/') >= 0){
      return Promise.resolve(new Response('{}', {
        status: 200, headers: {'Content-Type': 'application/json'}
      }));
    }
    return origFetch(input, init);
  };
})();
"""

TIMELINE_JS = r"""
async () => {
  const out = {};
  const q = s => document.querySelector(s);
  const cards = () => document.querySelectorAll('#libGrid .lib-card').length;
  const qasRows = () => document.querySelectorAll('#qasTaskList .lib-row:not(.lib-head)').length;
  const loaded = () => cards() > 0 && !q('#libGrid .lib-card-skeleton');
  const firstPaint = () => {
    const hint = q('#libList .lib-loading-hint');
    if (hint && hint.textContent.trim()) return true;
    return cards() > 0 || !!q('#libList .empty-title');
  };
  const wait = fn => new Promise(resolve => {
    const t0 = performance.now();
    const tick = () => {
      if (fn()) return resolve(performance.now() - t0);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  // 1) 首帧反馈（切换 → 骨架出现）：这段必须**先量时间**，
  //    因为「首帧占位高度」要在切页之后才存在（此前媒体库页是 display:none，
  //    隐藏元素的 #libList 高度会被子元素撑开，不能代表首帧占位）。
  switchTab('library');
  out.first_paint = Math.round(await wait(firstPaint));
  const el0 = q('#libList');
  const h0 = el0 ? Math.round(el0.getBoundingClientRect().height) : null;
  // 2) 目录可见（骨架被真实卡片替换）
  out.directory_ready = Math.round(await wait(loaded));
  out.cards = cards();
  // 3) 任务表可见
  out.qas_ready = Math.round(await wait(() => qasRows() > 0));
  out.qasRows = qasRows();
  // 4) 再次打开（切走再切回，页面不刷新）
  switchTab('overview');
  await new Promise(r => setTimeout(r, 150));
  switchTab('library');
  out.reopen_first_paint = Math.round(await wait(firstPaint));
  out.reopen_directory_ready = Math.round(await wait(loaded));
  out.libCountText = q('#libCount') ? q('#libCount').textContent : '';
  out.qasCountText = q('#qasTaskCount') ? q('#qasTaskCount').textContent : '';
  out.errorText = q('#libList .empty-title') ? q('#libList .empty-title').textContent : '';
  const el1 = q('#libList');
  out.firstH = h0;
  out.afterH = el1 ? Math.round(el1.getBoundingClientRect().height) : null;
  return out;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.8,
                    help="模拟上游（QAS 实查夸克目录）的每请求耗时，秒")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-open-ms", type=int, default=0,
                    help="目录可见耗时上限；默认按 --delay 推导（delay*1000 + 1500）")
    ap.add_argument("--max-shift-px", type=int, default=280,
                    help="首帧高度位移上限（px）；默认 280")
    args = ap.parse_args()
    budget_ms = args.max_open_ms or int(args.delay * 1000 + 1500)

    if not os.path.isfile(DIST):
        print("跳过：未找到构建产物 static/index_new.html")
        return 0
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("跳过：未安装 playwright（pip install playwright && python -m playwright install chromium）")
        return 0

    httpd = _start_server()
    report, problems = {}, []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"], **_launch_kwargs())
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            # 预置骨架数量，让首帧占位高度可复现（否则首帧用默认 8 行）
            page.add_init_script(
                "try{localStorage.setItem('auth_token','t');"
                "localStorage.setItem('lib_sk_rows','12');}catch(e){}")
            page.add_init_script(STUB_JS % {
                "delay": int(args.delay * 1000), "dirs": DIR_COUNT, "files": FILE_COUNT})
            page.goto(URL, wait_until="load", timeout=20000)
            # 等启动流程挂好全局函数（init() 是 async 的，会先拉配置再切页）
            page.wait_for_function("() => typeof switchTab === 'function'", timeout=10000)
            page.wait_for_timeout(200)
            report.update(page.evaluate(TIMELINE_JS))
            browser.close()
    finally:
        httpd.shutdown()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("媒体库打开耗时（桩接口延迟 {:.2f}s，模拟 QAS 实查夸克目录）".format(args.delay))
        for k, label in (("first_paint", "首帧反馈"),
                         ("directory_ready", "目录可见"),
                         ("qas_ready", "任务表可见"),
                         ("reopen_first_paint", "再次打开·首帧"),
                         ("reopen_directory_ready", "再次打开·目录可见")):
            if report.get(k) is not None:
                print("  {:<20} {:>6} ms".format(label, report[k]))
        print("  卡片 {} 张 / 任务 {} 行 / 计数 {!r}".format(
            report.get("cards"), report.get("qasRows"), report.get("libCountText")))
        if report.get("firstH") is not None and report.get("afterH") is not None:
            print("  首帧高度 {}px → 数据到达 {}px（位移 {}px）".format(
                report.get("firstH"), report.get("afterH"),
                report["afterH"] - report["firstH"]))

    # ---- 断言 ----
    if not report.get("directory_ready") or report["directory_ready"] < 0:
        problems.append("目录始终没有渲染出卡片（errorText={!r}，计数={!r}）".format(
            report.get("errorText"), report.get("libCountText")))
    elif report["directory_ready"] > budget_ms:
        problems.append("目录可见耗时 {}ms 超过上限 {}ms".format(
            report["directory_ready"], budget_ms))
    if not report.get("first_paint") or report["first_paint"] < 0:
        problems.append("切页后没有出现任何可读的首帧反馈（白屏）")
    elif report["first_paint"] > 400:
        problems.append("首帧反馈耗时 {}ms 超过 400ms（用户会先看到空白）".format(
            report["first_paint"]))
    if not report.get("qas_ready") or report["qas_ready"] < 0:
        problems.append("QAS 任务表始终没有渲染")
    elif report["qas_ready"] > budget_ms * 2:
        problems.append("任务表可见耗时 {}ms 异常（疑似被目录链路串行阻塞）".format(
            report["qas_ready"]))
    # 首帧占位高度必须接近数据高度：否则数据到达时整页下移，
    # 用户刚点下的按钮被推走（观感 = 卡、难用）
    if report.get("firstH") is not None and report.get("afterH") is not None:
        shift = report["afterH"] - report["firstH"]
        report["shift"] = shift
        if abs(shift) > args.max_shift_px:
            problems.append("首帧高度 {}px → 数据到达 {}px，位移 {}px 超过上限 {}px".format(
                report["firstH"], report["afterH"], shift, args.max_shift_px))
    # 再次打开必须显著更快（前端目录缓存命中）
    if (report.get("reopen_directory_ready") or -1) > 0 and (report.get("directory_ready") or -1) > 0:
        if report["reopen_directory_ready"] > report["directory_ready"]:
            problems.append("再次打开更慢（{}ms vs {}ms），缓存未生效".format(
                report["reopen_directory_ready"], report["directory_ready"]))
        elif report["reopen_directory_ready"] > args.delay * 1000:
            problems.append("再次打开仍耗时 {}ms（不低于一次上游延迟 {}ms），缓存未生效".format(
                report["reopen_directory_ready"], int(args.delay * 1000)))

    if problems:
        print("\n发现 {} 处问题：".format(len(problems)))
        for x in problems:
            print("  - " + x)
        return 1
    print("\n媒体库打开速度走查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
