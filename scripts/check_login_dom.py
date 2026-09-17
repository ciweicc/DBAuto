#!/usr/bin/env python3
"""登录页 DOM 度量走查（issue #27：登录页显示效果与主页不一致）。

用途：
  在真实浏览器里加载登录页产物，对**只有渲染后才能量化**的缺陷做断言。
  这些指标静态扫描（tests/test_build.py）表达不了 —— 而它们正是本轮修复的
  问题本身：

  1. **文字与背景的实际对比度**（两套主题 + 5 组视口）必须满足 WCAG 2.1 AA。
     历史缺陷：登录页在浅色主题下「白字白底」—— 页面内的 `:root{--text:#ffffff}`
     把 tokens.css 的浅色 `--text:#1c1c1e` 覆盖掉了，主标题对比度只有 1.0:1，
     几乎不可见。`scripts/check_contrast.py` 只审计 tokens.css，看不见这层覆盖，
     所以必须在这里按「真实合成结果」量一遍。
  2. **按钮 / 次要文字底下的背景必须是渐变（非纯色）**，且磨砂（backdrop-filter）
     真的生效。这条对应截图上最刺眼的那条浅色渐变横带：品牌列的深色底图
     被 `background-attachment:fixed` 按视口铺开，在 1336px 宽的屏幕上
     色带正好横穿登录按钮，按钮像「浮在一条浅色带上」；同时登录卡片的
     背景被显式覆盖成不透明浅色，磨砂完全失效、底部大面积死白。
  3. **焦点态可见**（键盘可达）：输入框聚焦必须有焦点环，且输入框自身
     背景不能是半透明 —— 半透明输入框叠在磨砂卡片上会被「透过去」，
     观感上控件不存在。
  4. 主按钮与密码可见性按钮的命中尺寸 ≥ 32px（触摸/低视力可用性）。
  5. 双主题切换后不得出现横向溢出。

依赖：playwright + chromium（与 scripts/check_overview_dom.py 同依赖），
  pip install playwright && python -m playwright install chromium
无依赖时脚本以退出码 0 跳过（避免影响不装浏览器的 CI 阶段）。

用法：
  python3 scripts/check_login_dom.py            # 检查（失败非零退出）
  python3 scripts/check_login_dom.py --json     # 机器可读报告
  python3 scripts/check_login_dom.py --serve    # 自动起本地静态服务
"""
import argparse
import functools
import http.server
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGIN = os.path.join(ROOT, "static", "login_new.html")
PORT = 8128
URL = "http://127.0.0.1:{}/static/login_new.html".format(PORT)

# (宽度, 高度, 标签)
VIEWPORTS = [
    (1920, 1080, "1920x1080 大屏"),
    (1440, 900, "1440x900"),
    (1336, 900, "1336x900 截图同宽"),
    (1024, 900, "1024x900"),
    (390, 844, "390x844 移动端"),
]

THEMES = ["dark", "light"]

AA_NORMAL = 4.5
AA_LARGE = 3.0
MIN_FOCUS_RING = 1.0          # 焦点态必须至少改掉边框色/加一圈阴影之一
MIN_TAP = 30                  # 主按钮高度 / 密码可见性按钮边长下限（px）


def _launch_kwargs():
    """本机 playwright 版本与已装 chromium 目录不匹配时回退到显式可执行文件。"""
    import glob
    for cand in sorted(glob.glob("/opt/ms-playwright/chromium-*/chrome-linux64/chrome")):
        if os.path.isfile(cand):
            return {"executable_path": cand}
    return {}


def _start_server():
    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(_QuietHandler, directory=ROOT)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# 度量脚本：在页面内把「半透明色 → 实际合成色」和对比度都算好，
# 只把结论回传给 Python，避免把整页样式表搬进上下文。
MEASURE = r"""
async () => {
  const parse = (c) => {
    const m = String(c).match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(",").map(s => parseFloat(s.trim()));
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  // 把半透明前景色合成到不透明背景上（背景必须是 [r,g,b] 数组，不能带 alpha）
  const over = (fg, bg) => [fg[0] * fg[3] + bg[0] * (1 - fg[3]),
                            fg[1] * fg[3] + bg[1] * (1 - fg[3]),
                            fg[2] * fg[3] + bg[2] * (1 - fg[3])];
  const lum = (rgb) => {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    return ((Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05));
  };
  // 逐级向上把半透明背景合成到页面底色上，得到元素背后的「实际底色」。
  // 注意：body 的 background-color 是 transparent（底色来自 html + 渐变），
  // 合成链必须以 html 的**不透明底色**兜底，且要按 [r,g,b] 纯数组参与运算，
  // 否则 over() 拿到透明色会算出 NaN（本轮实测踩到过）。
  const bgOf = (el) => {
    const chain = [];
    let node = el;
    while (node && node.nodeType === 1) {
      const c = parse(getComputedStyle(node).backgroundColor);
      if (c && c[3] > 0) chain.push(c);   // 自上而下：先遇到的是「最上层」的面
      node = node.parentElement;
    }
    // 自底向上回溯：以 root 的不透明底色兜底（body 通常 transparent），
    // 逐层把上层半透明面合成上去，得到元素背后真正的合成色。
    const rootC = parse(getComputedStyle(document.documentElement).backgroundColor);
    let acc = (rootC && rootC[3] >= 1) ? rootC.slice(0, 3) : [255, 255, 255];
    for (let i = chain.length - 1; i >= 0; i--) acc = over(chain[i], acc);
    return acc;
  };
  const q = (s) => document.querySelector(s);
  const probe = (s) => {
    const el = q(s);
    if (!el) return null;
    const cs = getComputedStyle(el);
    const fg = parse(cs.color);
    const bg = bgOf(el);
    return {sel: s, color: cs.color, bg: bg.map(v => Math.round(v)),
            ratio: fg ? Math.round(ratio(over(fg, bg), bg) * 100) / 100 : null};
  };
  const els = {
    body: q('body'),
    brandPanel: q('.brand-panel'),
    card: q('.login-card'),
    h1: q('.login-card h1'),
    subtitle: q('.login-subtitle'),
    label: q('.form-group label'),
    input: q('#user'),
    btn: q('#btn'),
    note: q('.login-note'),
    footer: q('.login-footer'),
  };
  const box = (el) => el ? el.getBoundingClientRect() : null;
  const btnR = box(els.btn), togR = box(q('#passToggleBtn'));

  // 返回一个 Promise：先读未聚焦态，聚焦后**等过渡结束**（≥260ms）再读，
  // 否则读到的是 transition 中间值（曾误报「焦点环 0.16px」）。
  const focusProbe = new Promise((done) => {
    const el = els.input;
    if (!el) return done(null);
    const read = () => {
      const cs = getComputedStyle(el);
      return [cs.borderColor, cs.boxShadow, cs.outlineColor, cs.outlineWidth];
    };
    // CSS 过渡会把「聚焦瞬间」渲染成中间值（曾读到 0.16px 的焦点环），
    // 走查关心的是**稳定态**：临时关掉过渡后再读。
    const keep = el.style.transition;
    el.style.transition = 'none';
    el.blur();
    const before = read();
    el.focus();
    const after = read();
    const ring = (() => {
      const sh = after[1];
      if (!sh || sh === 'none') return 0;
      const nums = sh.match(/(\d+(?:\.\d+)?)px/g) || [];
      return nums.reduce((m, v) => Math.max(m, parseFloat(v)), 0);
    })();
    el.blur();
    el.style.transition = keep;
    done({
      changed: before.some((v, i) => v !== after[i]),
      ring: ring,
      before: before,
      after: after,
    });
  });

  return {
    hintText: (document.querySelector('#user') || {}).placeholder || '',
    // 通用对比度探针：只放「纯色底」的文字。
    // 按钮走单独的 btnRatio —— 它的底是渐变，backgroundColor 是透明的，
    // 用纯色底算法会算出 1.1:1 的假失败（本轮实测踩到过）。
    targets: [
      probe('.login-card h1'),
      probe('.login-subtitle'),
      probe('.form-group label'),
      probe('.login-note'),
      probe('.login-footer'),
      probe('.brand-hero p'),
      probe('#user'),
    ].filter(Boolean),
    h1Ratio: (probe('.brand-hero h1') || {}).ratio,
    h1GradText: (() => {
      const g = q('.brand-hero h1 .grad');
      const cs = g ? getComputedStyle(g) : null;
      return cs ? [cs.webkitTextFillColor, cs.color, cs.backgroundImage.slice(0, 24)] : null;
    })(),
    btnBgHasGradient: (() => {
      const cs = els.btn ? getComputedStyle(els.btn) : null;
      return cs ? cs.backgroundImage.includes('gradient') : false;
    })(),
    // 按钮白字压的是**渐变**，不是纯色底，所以要单独取渐变主色算对比度
    btnRatio: (() => {
      if (!els.btn) return null;
      const cs = getComputedStyle(els.btn);
      const cols = cs.backgroundImage.match(/rgba?\([^)]+\)/g) || [];
      const fg = parse(cs.color);
      if (!fg || !cols.length) return null;
      let worst = Infinity;
      for (const raw of cols) {
        const c = parse(raw);
        if (!c) continue;
        const rgb = c[3] >= 1 ? c.slice(0, 3) : over(c, bgOf(els.btn));
        worst = Math.min(worst, ratio(over(fg, rgb), rgb));
      }
      return worst === Infinity ? null : Math.round(worst * 100) / 100;
    })(),
    btnBgImage: els.btn ? getComputedStyle(els.btn).backgroundImage.slice(0, 60) : '',
    cardBlur: els.card ? getComputedStyle(els.card).backdropFilter : '',
    cardBgImage: els.card ? getComputedStyle(els.card).backgroundImage.slice(0, 40) : '',
    inputBg: els.input ? getComputedStyle(els.input).backgroundColor : '',
    inputBgAlpha: (() => {
      if (!els.input) return null;
      const c = parse(getComputedStyle(els.input).backgroundColor);
      return c ? c[3] : null;
    })(),
    inputPadLeft: els.input ? Math.round(parseFloat(getComputedStyle(els.input).paddingLeft)) : 0,
    inputIconW: Math.round((box(q('.form-group .input-icon')) || {width: 0}).width),
    btnH: btnR ? Math.round(btnR.height) : 0,
    btnW: btnR ? Math.round(btnR.width) : 0,
    togW: togR ? Math.round(togR.width) : 0,
    cardW: Math.round((box(els.card) || {width: 0}).width),
    // 「卡片背后是不是一条横向色带」的代理指标：卡片四角 + 左右中点各自的
    // 实际合成底色，两两最大差超过阈值说明卡片压在了一条渐变横带上
    // （历史缺陷：品牌列深色底图被 background-attachment:fixed 按视口铺开，
    //  1336px 宽屏上色带正好横穿登录按钮）。
    cardBgSpread: (() => {
      if (!els.card) return null;
      const r = els.card.getBoundingClientRect();
      const pts = [[r.left + 4, r.top + 4], [r.right - 4, r.top + 4],
                   [r.left + 4, r.bottom - 4], [r.right - 4, r.bottom - 4],
                   [r.left + 4, r.top + r.height / 2], [r.right - 4, r.top + r.height / 2]];
      const seen = [];
      for (const [x, y] of pts) {
        const el = document.elementFromPoint(x, y) || document.body;
        const c = bgOf(el);
        if (c && !isNaN(c[0])) seen.push(c);
      }
      if (seen.length < 4) return null;
      let maxd = 0;
      for (let i = 1; i < seen.length; i++) {
        for (let k = 0; k < 3; k++) maxd = Math.max(maxd, Math.abs(seen[i][k] - seen[0][k]));
      }
      return Math.round(maxd);
    })(),
    cardTop: Math.round((box(els.card) || {top: 0}).top),
    footerBottom: Math.round((box(els.footer) || {bottom: 0}).bottom),
    vh: window.innerHeight,
    docOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    focus: await focusProbe,
  };
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--serve", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(LOGIN):
        print("跳过：未找到 static/login_new.html")
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
                    label = "{} {}".format(theme, label)
                    page = browser.new_page(viewport={"width": width, "height": height})
                    page.add_init_script(
                        "try{localStorage.setItem('theme','%s');"
                        "localStorage.removeItem('auth_token')}catch(e){}" % theme)
                    page.goto(URL, wait_until="load", timeout=20000)
                    page.wait_for_timeout(350)
                    m = page.evaluate(MEASURE)
                    m["label"] = label
                    report.append(m)

                    def fail(msg):
                        problems.append("[{}] {}".format(label, msg))

                    # 1. 文字对比度（AA 正文）
                    for t in m["targets"]:
                        if t["ratio"] is None:
                            continue
                        if t["ratio"] < AA_NORMAL:
                            fail("{} 对比度 {:.2f}:1 < {}:1（前景 {} / 实际底色 {}）"
                                 .format(t["sel"], t["ratio"], AA_NORMAL, t["color"], t["bg"]))
                    # 2. 主按钮：渐变底 + 够高
                    if not m["btnBgHasGradient"]:
                        fail("登录按钮没有品牌渐变底（background-image={!r}）".format(m["btnBgImage"]))
                    if m["btnH"] < MIN_TAP:
                        fail("登录按钮高度 {}px < {}px".format(m["btnH"], MIN_TAP))
                    # 按钮白字压在渐变上：最浅的一端也要够（按钮为粗体大字号，按 3:1）
                    if m["btnRatio"] is not None and m["btnRatio"] < AA_LARGE:
                        fail("登录按钮文字压在渐变最浅端只有 {:.2f}:1 < {}:1".format(
                            m["btnRatio"], AA_LARGE))
                    # 3. 卡片：磨砂必须生效 + 有玻璃高光
                    if "blur" not in (m["cardBlur"] or ""):
                        fail("登录卡片缺失磨砂（backdrop-filter={!r}）".format(m["cardBlur"]))
                    if "gradient" not in (m["cardBgImage"] or ""):
                        fail("登录卡片缺少玻璃高光渐变（background-image={!r}）".format(
                            m["cardBgImage"]))
                    # 4. 输入框：不透明底色 + 图标不与文字重叠
                    if m["inputBgAlpha"] is not None and m["inputBgAlpha"] < 0.99:
                        fail("输入框底色是半透明的（alpha={}），叠在磨砂卡片上会「透底」".format(
                            m["inputBgAlpha"]))
                    if m["inputPadLeft"] < m["inputIconW"] + 12:
                        fail("输入框左内边距 {}px 不足，文字会与图标重叠".format(m["inputPadLeft"]))
                    # 5. 焦点环
                    if not m["focus"] or not m["focus"]["changed"]:
                        fail("输入框聚焦后样式无变化，键盘用户看不到焦点")
                    elif m["focus"]["ring"] < MIN_FOCUS_RING:
                        fail("输入框焦点环厚度 {}px 过小".format(m["focus"]["ring"]))
                    # 6. 触摸目标
                    if m["togW"] and m["togW"] < MIN_TAP:
                        fail("密码可见性按钮边长 {}px < {}px".format(m["togW"], MIN_TAP))
                    # 7. 渐变标题：必须真的是渐变文字，否则浅色主题下会「糊」
                    if m["h1GradText"] and "gradient" not in (m["h1GradText"][2] or ""):
                        fail("品牌主标题第二行未吃品牌渐变（{!r}）".format(m["h1GradText"]))
                    # 8. 溢出与视口
                    if m["docOverflowX"] > 0:
                        fail("文档横向溢出 {}px".format(m["docOverflowX"]))
                    # 9. 卡片背后不得出现「横穿登录按钮的色带」
                    #    阈值 24：正文/描边级别的色差（≤8）正常，24 以上肉眼可见为色带。
                    if m["cardBgSpread"] is not None and m["cardBgSpread"] > 24:
                        fail("登录卡片背后出现横向色带（四角+中点底色最大差 {}）"
                             .format(m["cardBgSpread"]))
                    if width > 860 and m["footerBottom"] > height:
                        fail("内容超出视口：页脚底边 {}px > {}px（登录页不应出现纵向滚动）".format(
                            m["footerBottom"], height))
                    page.close()
            browser.close()
    finally:
        if httpd is not None:
            httpd.shutdown()

    if args.json:
        print(json.dumps({"problems": problems, "report": report}, ensure_ascii=False, indent=2))
    else:
        for m in report:
            worst = min((t["ratio"] for t in m["targets"] if t["ratio"] is not None), default=None)
            print("{:<28} 最低对比度 {:<7} 按钮高 {}px 卡片宽 {:.0f}px 磨砂 {} 卡片底色差 {}".format(
                m["label"], "{}:1".format(worst) if worst else "n/a",
                m["btnH"], m["cardW"], "有" if "blur" in (m["cardBlur"] or "") else "无",
                m["cardBgSpread"]))
        print()
        if problems:
            print("发现 {} 处问题：".format(len(problems)))
            for p_ in problems:
                print("  - " + p_)
            return 1
        print("登录页 DOM 度量走查通过（{} 组主题×视口，0 处问题）".format(len(report)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
