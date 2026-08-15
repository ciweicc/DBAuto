#!/usr/bin/env python3
"""DBAuto frontend build script (Python equivalent of build.sh)"""
import os, subprocess, sys

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_FILE = os.path.join(SRC_DIR, "..", "index_new.html")

print("Building DBAuto frontend...")

# Combine CSS
css_parts = []
tokens_path = os.path.join(SRC_DIR, "styles", "tokens.css")
main_css_path = os.path.join(SRC_DIR, "styles", "main.css")
if os.path.isfile(tokens_path) and os.path.isfile(main_css_path):
    with open(tokens_path, "r", encoding="utf-8") as f:
        css_parts.append(f.read())
    with open(main_css_path, "r", encoding="utf-8") as f:
        css_parts.append(f.read())
elif os.path.isfile(main_css_path):
    with open(main_css_path, "r", encoding="utf-8") as f:
        css_parts.append(f.read())
css_content = "\n".join(css_parts)

# Combine JS
js_files = [
    "auth", "animation", "theme", "globals", "sound", "toast", "confirm",
    "tabs", "categories", "transfer", "log", "schedule", "history",
    "settings", "tmdb", "search", "dashboard", "overview", "init"
]
js_parts = []
for f in js_files:
    p = os.path.join(SRC_DIR, "scripts", f + ".js")
    if not os.path.isfile(p):
        print(f"ERROR: Missing module {p}", file=sys.stderr)
        sys.exit(1)
    with open(p, "r", encoding="utf-8") as fh:
        js_parts.append(f"// ===== module: {f}.js =====\n{fh.read()}\n")
js_content = "\n".join(js_parts)

# Read body
with open(os.path.join(SRC_DIR, "body.html"), "r", encoding="utf-8") as f:
    body_content = f.read()

# Git SHA
git_sha = "unknown"
try:
    git_sha = subprocess.check_output(
        ["git", "-C", SRC_DIR, "rev-parse", "--short", "HEAD"],
        stderr=subprocess.DEVNULL
    ).decode("utf-8").strip()
except Exception:
    pass
fingerprint = f"sha:{git_sha}"

# Assemble HTML
html = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>豆瓣自动转存</title>
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<script>
/* FOUC 防闪烁：在 CSS 之前同步设置主题 */
(function(){{var t=localStorage.getItem('theme')||'dark';document.documentElement.setAttribute('data-theme',t)}})();
</script>
<style>
{css_content}
</style>
</head>
<body>
{body_content}

<script>
// ===== DBAuto frontend bundle =====
// {fingerprint}
// 由 build 脚本拼接生成（模块化源文件合并为单文件）。
// 注意：不使用 IIFE 包裹。页面大量使用内联 onclick/onchange 事件处理器，
// 这些处理器在全局作用域查找被调用的函数；若包进 IIFE 会导致函数不可见、按钮点击失效。
{js_content}
</script>
</body>
</html>
"""

with open(DIST_FILE, "w", encoding="utf-8", newline="\n") as f:
    f.write(html)

size = os.path.getsize(DIST_FILE)
print(f"Built: {DIST_FILE}")
print(f"Size: {size} bytes")
