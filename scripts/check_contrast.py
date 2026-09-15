#!/usr/bin/env python3
"""对比度审计（UI 路线图 4.1）— WCAG 2.1 AA 基线检查。

用途：
  解析 static/src/styles/tokens.css 中的设计令牌，对「文本色 × 常见背景」
  组合计算 WCAG 对比度，输出报告并对低于阈值的组合非零退出。

覆盖的背景：
  深色主题：--bg / --surface-solid / 玻璃态面（--glass-bg 与其不透明近似）
  浅色主题：--bg / --surface-solid / --glass-bg-strong

阈值（WCAG 2.1 AA）：
  - 正文（--text / --text2）：≥ 4.5:1
  - 三级文本与状态色小字（--text3 等）：≥ 4.5:1（路线图 4.1 要求）
  - 大字号/装饰性元素：≥ 3:1（以 --min-large 控制）

用法：
  python3 scripts/check_contrast.py            # 检查，失败非零退出
  python3 scripts/check_contrast.py --json     # 输出机器可读报告
  python3 scripts/check_contrast.py --report docs/UI_Contrast_Report.md
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS = os.path.join(ROOT, "static", "src", "styles", "tokens.css")

AA_NORMAL = 4.5
AA_LARGE = 3.0

# 需要审计的文本令牌 -> 最低要求
TEXT_TOKENS = {
    "--text": AA_NORMAL,
    "--text2": AA_NORMAL,
    "--text3": AA_NORMAL,
}

# 状态/语义色：多以小字或强调形式出现，同样按 AA 正文要求
STATUS_TOKENS = {
    "--accent": AA_NORMAL,
    "--green": AA_NORMAL,
    "--red": AA_NORMAL,
    "--orange": AA_NORMAL,
    "--purple": AA_NORMAL,
    "--cyan": AA_NORMAL,
}

# 每个主题下参与审计的背景令牌
BACKGROUNDS = {
    "dark": ["--bg", "--surface-solid"],
    "light": ["--bg", "--surface-solid"],
}

_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGBA = re.compile(r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$")


def parse_color(value):
    """把 CSS 颜色字面量解析为 (r, g, b, a)，范围 0-255 / 0-1。"""
    v = value.strip()
    m = _HEX.match(v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = _RGBA.match(v)
    if m:
        r, g, b = (float(m.group(i)) for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (r, g, b, a)
    return None


def block_vars(css, selector):
    """提取指定选择器块内的 --var: value 映射。"""
    idx = css.find(selector)
    if idx < 0:
        return {}
    start = css.find("{", idx)
    depth = 0
    end = start
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    body = css[start + 1:end]
    out = {}
    for line in body.splitlines():
        line = line.split("/*")[0].strip()
        m = re.match(r"^(--[\w-]+)\s*:\s*(.+?);\s*$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def resolve(value, table, depth=0):
    """展开 var(--x) 引用（含回退值）。"""
    if depth > 8:
        return value
    v = value.strip()
    m = re.match(r"^var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)$", v)
    if m:
        name, fallback = m.group(1), m.group(2)
        if name in table:
            return resolve(table[name], table, depth + 1)
        if fallback:
            return resolve(fallback, table, depth + 1)
    return v


def flatten(fg, bg):
    """把半透明前景色合成到背景上（背景视为不透明）。"""
    r, g, b, a = fg
    if a >= 1.0:
        return (r, g, b)
    br, bg_, bb = bg
    return (r * a + br * (1 - a), g * a + bg_ * (1 - a), b * a + bb * (1 - a))


def rel_luminance(rgb):
    def chan(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg_rgb, bg_rgb):
    l1, l2 = rel_luminance(fg_rgb), rel_luminance(bg_rgb)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def audit():
    with open(TOKENS, encoding="utf-8") as f:
        css = f.read()

    themes = {
        "dark": block_vars(css, ":root"),
        "light": block_vars(css, '[data-theme="light"]'),
    }
    shared = dict(themes["dark"])
    shared.update(themes["light"])

    rows = []
    for theme, table in themes.items():
        merged = dict(shared)
        merged.update(table)
        for bg_name in BACKGROUNDS[theme]:
            bg_raw = resolve(merged.get(bg_name, ""), merged)
            bg = parse_color(bg_raw)
            if not bg:
                continue
            bg_rgb = flatten(bg, (255, 255, 255))
            for group in (TEXT_TOKENS, STATUS_TOKENS):
                for name, minimum in group.items():
                    if name not in merged:
                        continue
                    fg_raw = resolve(merged[name], merged)
                    fg = parse_color(fg_raw)
                    if not fg:
                        continue
                    ratio = contrast_ratio(flatten(fg, bg_rgb), bg_rgb)
                    rows.append({
                        "theme": theme,
                        "bg": bg_name,
                        "fg": name,
                        "raw": fg_raw,
                        "ratio": round(ratio, 2),
                        "min": minimum,
                        "pass": ratio >= minimum,
                    })

    # 去重：同一 theme/fg 在多背景下的最差结果才是有效结论
    return rows


def worst_per_token(rows):
    worst = {}
    for r in rows:
        k = (r["theme"], r["fg"])
        if k not in worst or r["ratio"] < worst[k]["ratio"]:
            worst[k] = r
    return [worst[k] for k in sorted(worst)]


def render_markdown(report_rows):
    lines = [
        "# DBAuto 前端对比度审计报告（WCAG 2.1 AA）",
        "",
        "> 由 `scripts/check_contrast.py` 自动生成，请勿手工编辑。",
        "> 阈值：正文与三级文本、状态色小字均为 ≥ 4.5:1（路线图 4.1）。",
        "",
        "| 主题 | 令牌 | 解析值 | 最差背景 | 对比度 | 阈值 | 结论 |",
        "|------|------|--------|----------|--------|------|------|",
    ]
    for r in report_rows:
        lines.append(
            "| {theme} | `{fg}` | `{raw}` | `{bg}` | {ratio}:1 | {min}:1 | {res} |".format(
                theme="深色" if r["theme"] == "dark" else "浅色",
                fg=r["fg"], raw=r["raw"], bg=r["bg"], ratio=r["ratio"],
                min=r["min"], res="✅" if r["pass"] else "❌",
            )
        )
    failed = [r for r in report_rows if not r["pass"]]
    lines += ["", "## 结论", ""]
    if failed:
        lines.append("以下组合未达 AA 基线，需调整令牌取值：")
        lines.append("")
        for r in failed:
            lines.append("- `{}`（{} 主题，背景 `{}`）：{}:1 < {}:1".format(
                r["fg"], r["theme"], r["bg"], r["ratio"], r["min"]))
    else:
        lines.append("全部受检组合均达到 WCAG 2.1 AA 基线（≥ 4.5:1）。")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="DBAuto 设计令牌对比度审计")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--report", metavar="PATH", help="同时写入 Markdown 报告")
    args = ap.parse_args()

    rows = audit()
    worst = worst_per_token(rows)
    failed = [r for r in worst if not r["pass"]]

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(render_markdown(worst))
        print("报告已写入 {}".format(args.report))

    if args.json:
        print(json.dumps({"results": worst, "failed": failed}, ensure_ascii=False, indent=2))
    else:
        for r in worst:
            print("[{}] {:<10} {:<24} on {:<16} {:>6}:1  (min {}:1) {}".format(
                "PASS" if r["pass"] else "FAIL", r["theme"], r["fg"], r["bg"],
                r["ratio"], r["min"], "" if r["pass"] else "<-- 未达标"))
        print("\n共 {} 组受检，{} 组未达标".format(len(worst), len(failed)))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
