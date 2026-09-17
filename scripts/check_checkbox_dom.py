#!/usr/bin/env python3
"""复选 / 单选控件 DOM 度量走查（issue #27 反馈：复选框有点丑）。

用途：
  勾选框的「丑」和「三种长相」是**只有渲染后才能量化**的问题，
  静态扫描（tests/test_build.py）只能看出「有没有写规则」，看不出：

  1. 浏览器是否真的按自绘样式渲染（`appearance` 被重置、尺寸不受 font-size 影响）；
  2. 同一个页面里所有勾选框（卡片角标 / 表格行 / 全选）是否是**同一套形态**
     —— 历史问题：卡片上是 15px 系统蓝方块压深色圆底、表格里是 16px 原生框、
     全选又是一枚裸 input，三种长相；
  3. 已勾选 / 半选状态是否真的有可见的勾形与横杠（`:checked::after` 生效）；
  4. 深浅两套主题下的勾选块与白勾对比度是否 ≥3:1（WCAG 1.4.11 非文本对比度），
     以及勾选块与页面底色的边界对比度（浅色主题最容易糊）。

依赖：playwright + chromium（与 scripts/check_library_dom.py 同依赖）。
无依赖时脚本以退出码 0 跳过（避免影响不装浏览器的 CI 阶段）。

用法：
  python3 scripts/check_checkbox_dom.py            # 检查（失败非零退出）
  python3 scripts/check_checkbox_dom.py --json     # 机器可读报告
  python3 scripts/check_checkbox_dom.py --serve    # 自动起本地静态服务
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
PORT = 8134
URL = "http://127.0.0.1:{}/index_new.html".format(PORT)

# 只桩住媒体库页需要的接口：本脚本关心的是勾选框，不是数据链路。
_ENTRIES = [
    {"fid": "d1", "name": "示例作品标题-01", "dir": True, "size": 0, "updated_at": 1758000000},
    {"fid": "d2", "name": "无法匹配的文件夹", "dir": True, "size": 0, "updated_at": 1758000001},
    {"fid": "f1", "name": "示例作品标题-01.2024.2160p.mkv", "dir": False,
     "size": 8 * 1024 ** 3, "updated_at": 1758000100},
    {"fid": "f2", "name": "示例作品标题-02.mkv", "dir": False,
     "size": 1024 ** 3, "updated_at": 1758000200},
]
_QAS_TASKS = {"success": True, "tasks": [
    {"taskname": "示例任务-{:02d}".format(i), "savepath": "/媒体库/电影",
     "shareurl": "https://pan.quark.cn/s/demo{:02d}".format(i)}
    for i in range(3)
]}

STUBS = {
    "/api/categories": {"movie": [], "tv": [], "variety": []},
    "/api/dashboard/all": {"stats": {}, "schedule_status": {}, "version": "1.2.0"},
    "/api/history": {"total": 0, "items": {}},
    "/api/tmdb/list": {"items": []},
    "/api/exec_history": {"total": 0, "items": []},
    "/api/settings/all": {"config": {}, "schedule": {"_next_runs": {}, "savepaths": {}}},
    "/api/transfer/status": {"running": False, "progress": [], "stats": {}},
    "/api/qas/library": {"success": True, "path": "/", "entries": _ENTRIES},
    "/api/qas/tasks": _QAS_TASKS,
    "/api/tmdb/match": {"success": True, "matches": {}, "truncated": 0},
}

# 采样点：卡片角标 / 表格行 / 全选（媒体库）——三处历史上长得不一样
MEASURE = r"""
() => {
  const q = s => document.querySelector(s);
  const TRANSPARENT = /^(transparent|rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\))$/;
  const rgb = s => {
    s = String(s);
    // `transparent` / rgba(0,0,0,0) 是「没有底色」，不能当成黑色实底
    // （历史：把透明算成 [0,0,0,1] 后，浅色主题的填色被算成纯黑，整条断言失真）
    if(!s || TRANSPARENT.test(s.trim())) return null;
    const m = s.match(/rgba?\(([^)]+)\)/);
    if(!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x));
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const hex = s => {
    const m = String(s).trim().match(/^#([0-9a-f]{6})$/i);
    if(!m) return null;
    const v = parseInt(m[1], 16);
    return [(v >> 16) & 255, (v >> 8) & 255, v & 255, 1];
  };
  const over = (fg, bg) => {   // 半透明前景合成到背景上
    if(!fg || !bg) return null;
    const a = fg[3];
    return [fg[0]*a + bg[0]*(1-a), fg[1]*a + bg[1]*(1-a), fg[2]*a + bg[2]*(1-a), 1];
  };
  const lum = c => {
    const f = v => { v = v/255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); };
    return 0.2126*f(c[0]) + 0.7152*f(c[1]) + 0.0722*f(c[2]);
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    return Math.round(((Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05)) * 100) / 100;
  };
  // 元素背后「实际」底色：向上找第一个不透明背景（面板是半透明玻璃）。
  // body 的 backgroundColor 是 transparent（底色来自渐变图层），此时退化用
  // 主题令牌 --bg-deep —— 与用户肉眼看到的底色一致。
  const pageBg = () => rgb(getComputedStyle(document.body).backgroundColor)
    || hex(getComputedStyle(document.documentElement).getPropertyValue('--bg-deep'))
    || [0,0,0,1];
  const behind = el => {
    let bg = pageBg(), node = el.parentElement;
    const stack = [];
    while(node && node !== document.documentElement){
      const c = rgb(getComputedStyle(node).backgroundColor);
      if(c && c[3] > 0) stack.push(c);
      node = node.parentElement;
    }
    for(let i = stack.length - 1; i >= 0; i--) bg = over(stack[i], bg);
    return bg;
  };
  const pick = sel => {
    const el = q(sel);
    if(!el) return null;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const after = getComputedStyle(el, '::after');
    const bgRaw = rgb(cs.backgroundColor);
    const base = behind(el);
    const block = over(bgRaw, base) || base;  // 控件块面合成到背景上（原生控件时背景是透明的）
    const border = rgb(cs.borderColor);
    const borderOn = border ? over(border, block) : null;
    return {
      sel: sel,
      w: Math.round(r.width), h: Math.round(r.height),
      appearance: cs.appearance || cs.webkitAppearance,
      borderColor: cs.borderColor,
      radius: cs.borderRadius,
      checked: !!el.checked,
      indeterminate: !!el.indeterminate,
      afterContent: after.content,
      afterW: after.width,
      fill: block,
      base: base,
      // 勾形：::after 在**已勾选**时是白色描边（border-width 上色），
      // 未勾选时是 0px 边框 —— 按实际边框色与宽度取值，不能无条件用白色。
      markColor: rgb(after.borderTopColor) || rgb(after.backgroundColor) || [255,255,255,1],
      markW: parseFloat(after.borderTopWidth) || 0,
      // 勾形与块面的对比度（已勾选态才有效）
      gapRatio: ratio(rgb(after.borderTopColor) || rgb(after.backgroundColor) || [255,255,255,1], block),
      // 「控件可见性」= 块面或描边与页面底色的对比度取大者。
      // 浅色主题下块面是纯白、和卡片底几乎同色，真正让控件显形的是描边。
      edgeRatio: Math.max(
        ratio(block, base),
        borderOn ? ratio(borderOn, base) : 0
      ),
      borderRatio: borderOn ? ratio(borderOn, base) : 0,
    };
  };
  // 采样点可能存在但**有意隐藏**（没有条目时不该显示勾选框），
  // 这类样本只要求「不占版面」，不参与形状一致性比对。
  const visible = el => { const b = el.getBoundingClientRect(); return b.width > 0 && b.height > 0; };
  const all = [...document.querySelectorAll('input[type=checkbox],input[type=radio]')];
  // 采样时**先全部取消勾选**：未勾选态的块面/描边才是「这个控件在页面里显不显形」的依据。
  // （已勾选态另外单独量一次，见脚本里 checked 那一步。）
  all.forEach(e => { e.checked = false; e.indeterminate = false; });
  return {
    missing: !q('#libList'),
    samples: [
      Object.assign({}, pick('.lib-card-cb input'), {required: true}),
      Object.assign({}, pick('#qasTaskList .lib-cb input'), {required: true}),
      Object.assign({}, pick('#libSelAll'), {required: false}),
      Object.assign({}, pick('#qasSelAll'), {required: true}),
    ].map(x => x ? Object.assign(x, {visible: !!x.w && !!x.h}) : x),
    total: all.length,
    nativeCount: all.filter(e => {
      const a = getComputedStyle(e).appearance || getComputedStyle(e).webkitAppearance;
      return a !== 'none';
    }).length,
    // 尺寸是否被 font-size 影响：同一控件在不同字号容器下必须仍是同宽高
    sizeUnderZoom: (function(){
      const el = q('#libSelAll');
      if(!el) return null;
      const before = Math.round(el.getBoundingClientRect().width);
      const host = el.parentElement;
      const keep = host.style.fontSize;
      host.style.fontSize = '28px';
      const after = Math.round(el.getBoundingClientRect().width);
      host.style.fontSize = keep;
      return [before, after];
    })(),
  };
}
"""

_CHECKED = r"""
(sel) => {
  const el = document.querySelector(sel);
  if(!el) return null;
  el.checked = true;
  el.dispatchEvent(new Event('change', {bubbles:true}));
  const cs = getComputedStyle(el);
  const after = getComputedStyle(el, '::after');
  const rgb = s => { const m = String(s).match(/rgba?\(([^)]+)\)/); if(!m) return null;
    const p = m[1].split(',').map(Number); return [p[0],p[1],p[2],p.length>3?p[3]:1]; };
  const over = (fg,bg) => fg ? [fg[0]*fg[3]+bg[0]*(1-fg[3]), fg[1]*fg[3]+bg[1]*(1-fg[3]), fg[2]*fg[3]+bg[2]*(1-fg[3]), 1] : null;
  const lum = c => { const f = v => { v=v/255; return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4); };
    return 0.2126*f(c[0])+0.7152*f(c[1])+0.0722*f(c[2]); };
  const ratio = (a,b) => { const l1=lum(a),l2=lum(b);
    return Math.round(((Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05))*100)/100; };
  // 块面取值优先级：background-image（勾选态用的是 --gradient-btn 渐变，
  // `background-color` 在渐变之上**不生效**，直接读它会把勾选块量成白色）→
  // backgroundColor（未勾选态的实底）。两者都取不到才算黑。
  const firstStop = () => {
    const m = String(cs.backgroundImage).match(/rgba?\([^)]*\)/);
    return m ? rgb(m[0]) : null;
  };
  const block = firstStop() || rgb(cs.backgroundColor) || [0,0,0,1];
  // 勾形是 ::after 的白色描边（border-width:0 2px 2px 0 → borderTopWidth 是 0，
  // 必须取**最宽的那条边**，否则会误判成「没有勾形」）
  const widths = [after.borderTopWidth, after.borderRightWidth,
                  after.borderBottomWidth, after.borderLeftWidth].map(parseFloat);
  const markW = Math.max.apply(null, widths.filter(w => !isNaN(w)));
  const mark = rgb(after.borderTopColor) || rgb(after.backgroundColor) || [255,255,255,1];
  return {sel: sel, checked: !!el.checked, afterContent: after.content,
          markW: Math.round(markW), mark: mark, block: block,
          markRatio: ratio(mark, block)};
}
"""

_INDET = r"""
() => {
  const el = document.getElementById('libSelAll');
  if(!el) return null;
  // 半选：勾一部分（不全选、不全不选），全选框必须进入 indeterminate
  const boxes = [...document.querySelectorAll('.lib-card-cb input')];
  boxes.slice(0, 1).forEach(b => {
    if(!b.checked){ b.checked = true; b.dispatchEvent(new Event('change', {bubbles:true})); }
  });
  const cs = el;
  return {indeterminate: !!cs.indeterminate, checked: !!cs.checked};
}
"""

VIEWPORTS = [(1440, 900, "1440x900"), (390, 844, "390x844 移动端")]
THEMES = ["dark", "light"]
MIN_NON_TEXT = 3.0        # WCAG 1.4.11 非文本对比度


def _launch_kwargs():
    for cand in sorted(glob.glob("/opt/ms-playwright/chromium-*/chrome-linux64/chrome")):
        if os.path.isfile(cand):
            return {"executable_path": cand}
    return {}


def _start_server():
    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(_Quiet, directory=os.path.join(ROOT, "static"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _route(route):
    url = route.request.url
    if "/api/" not in url:
        return route.fallback()
    path = url.split(str(PORT), 1)[-1].split("?", 1)[0]
    if path.rstrip("/").endswith("/api/sse"):
        return route.fulfill(status=200, content_type="text/event-stream", body=b"")
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
            for theme in THEMES:
                for width, height, label in VIEWPORTS:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    page.add_init_script(
                        "try{localStorage.setItem('auth_token','t');"
                        "localStorage.setItem('theme','%s')}catch(e){}" % theme)
                    page.route("**/*", _route)
                    page.goto(URL, wait_until="load", timeout=20000)
                    page.evaluate("()=>switchTab('library')")
                    page.wait_for_timeout(1500)
                    m = page.evaluate(MEASURE)
                    m["label"] = "{} / {}".format(theme, label)
                    report.append(m)

                    tag = "[{} / {}]".format(theme, label)

                    def fail(msg):
                        problems.append("{} {}".format(tag, msg))

                    if m.get("missing"):
                        fail("页面缺少 #libList，媒体库未渲染")
                        page.close()
                        continue

                    # 1. 全部勾选框都必须走自绘外观（不得残留浏览器原生控件）
                    if m["total"] < 4:
                        fail("未找到足够的勾选框（{} 个），采样无效".format(m["total"]))
                    if m["nativeCount"] > 0:
                        fail("仍有 {} 个勾选框是浏览器原生外观（appearance 未重置）".format(
                            m["nativeCount"]))

                    # 2. 三处采样点必须存在，且尺寸/圆角/自绘态完全一致
                    samples = [s for s in m["samples"] if s and s["visible"]]
                    required_missing = [s for s in m["samples"] if s and s.get("required")
                                        and not s["visible"]]
                    if required_missing:
                        fail("勾选框采样点不可见：{}".format(
                            [s["sel"] for s in required_missing]))
                    if len(samples) < 3:
                        fail("勾选框采样点不足（{} 个）".format(len(samples)))
                    else:
                        shapes = {(s["w"], s["h"], s["radius"]) for s in samples}
                        if len(shapes) > 1:
                            fail("同一页勾选框尺寸/圆角不一致（三种长相）：{}".format(
                                sorted(shapes)))
                        for s in samples:
                            if s["w"] < 16 or s["h"] < 16:
                                fail("{} 勾选框过小（{}x{}），触屏点不准".format(
                                    s["sel"], s["w"], s["h"]))

                    # 3. 控件可见性：描边或块面**至少一个**要与页面底色分得开。
                    #    浅色主题下块面是纯白、和卡片底几乎同色，真正让控件显形的是深色描边；
                    #    所以阈值按「最好的那个通道」定，而不是要求块面本身有对比。
                    #    （历史：卡片角标自己在深色圆底上画控件，两种主题下都还行；
                    #      表格/全选是裸控件贴在浅色卡面上，白底白框 → 观感「没有控件」。）
                    for s in samples:
                        if s["edgeRatio"] < 1.4:
                            fail("{} 勾选框与背景几乎同色（可见性对比度 {:.2f}:1，"
                                 "块面 {:.2f} / 描边 {:.2f}），观感是「没有控件」".format(
                                     s["sel"], s["edgeRatio"],
                                     s["edgeRatio"], s["borderRatio"]))

                    # 4. 尺寸不受 font-size 影响（原生 checkbox 会被字号缩放）
                    z = m.get("sizeUnderZoom")
                    if z and z[0] != z[1]:
                        fail("字号变化后勾选框宽度从 {}px 变成 {}px，尺寸被 font-size 缩放".format(
                            z[0], z[1]))

                    # 5. 已勾选态：勾形是 ::after 画的，必须真的画出来且看得清
                    picked = page.evaluate(_CHECKED, ".lib-card-cb input")
                    page.wait_for_timeout(200)
                    if not picked or not picked["checked"]:
                        fail("卡片勾选框无法进入已勾选态（勾选链路断了）")
                    else:
                        if picked["afterContent"] in ("none", "normal", ""):
                            fail("已勾选态没有画出勾形（::after 未生效），勾了也看不出来")
                        elif picked["markW"] < 2:
                            fail("勾形描边宽度 {}px，几乎不可见".format(picked["markW"]))
                        elif picked["markRatio"] < MIN_NON_TEXT:
                            fail("勾形与勾选块对比度 {:.2f}:1 < {}:1，勾看不清".format(
                                picked["markRatio"], MIN_NON_TEXT))

                    # 6. 半选态（全选框的 indeterminate）必须有可见横杠
                    ind = page.evaluate(_INDET)
                    page.wait_for_timeout(200)
                    sel_all = page.evaluate(_CHECKED, "#libSelAll")
                    if ind and ind.get("indeterminate"):
                        if sel_all and sel_all["afterContent"] in ("none", "normal", ""):
                            fail("全选框半选态没有画出横杠（indeterminate 不可见）")
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
            shapes = ", ".join("{} {}x{}/{}".format(
                s["sel"].split(" ")[0], s["w"], s["h"], s["radius"])
                for s in m["samples"] if s)
            print("{:<22} 勾选框 {:<2} 个（原生 {}） 可见性对比度 {}  {}".format(
                m["label"], m["total"], m["nativeCount"],
                " ".join("{:.2f}".format(s["edgeRatio"]) for s in m["samples"] if s),
                shapes))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p_ in problems:
                print("  - " + p_)
            return 1
        print("复选/单选控件 DOM 度量走查通过（0 处问题）")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
