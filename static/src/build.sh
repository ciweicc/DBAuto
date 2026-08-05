#!/bin/bash
# DBAuto 前端构建脚本
# 将模块化的 CSS/JS 文件合并为单个 index_new.html
# 使用方法: bash build.sh
#
# 压缩：优先使用 esbuild（PATH 中的 esbuild 或 npx --yes esbuild）对 CSS/JS 做 minify；
#       若环境中均不可用，则原样透传（不阻断构建）。minify 只折叠局部变量与空白，
#       保留顶层全局函数名，因此内联 onclick/onchange 处理器仍可正常解析。

set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_FILE="$SRC_DIR/../index_new.html"

# ---------- 压缩工具 ----------
# minify <输入文件> <loader: js|css>  ->  输出到 stdout
# 通过 stdin 传入源码（避免 Windows 下 Git Bash 的 Unix 路径被 esbuild 误判扩展名），
# 并显式指定 --loader。优先级：本地 esbuild > npx esbuild > 原样透传（兜底）。
minify() {
  local infile="$1" loader="$2"
  if command -v esbuild >/dev/null 2>&1; then
    if esbuild --minify --loader="$loader" < "$infile" 2>/dev/null; then
      return 0
    fi
  fi
  if command -v npx >/dev/null 2>&1; then
    if npx --yes esbuild --minify --loader="$loader" < "$infile" 2>/dev/null; then
      return 0
    fi
  fi
  cat "$infile"
}

CSS_TMP=$(mktemp)
JS_TMP=$(mktemp)
trap 'rm -f "$CSS_TMP" "$JS_TMP"' EXIT

echo "Building DBAuto frontend..."

# 组合并压缩 CSS
if [ -f "$SRC_DIR/styles/tokens.css" ] && [ -f "$SRC_DIR/styles/main.css" ]; then
  cat "$SRC_DIR/styles/tokens.css" "$SRC_DIR/styles/main.css" > "$CSS_TMP"
elif [ -f "$SRC_DIR/styles/main.css" ]; then
  cat "$SRC_DIR/styles/main.css" > "$CSS_TMP"
else
  : > "$CSS_TMP"
fi
CSS_CONTENT=$(minify "$CSS_TMP" css)

# 组合并压缩 JS（按依赖顺序，顺序不可随意调整）
# 依赖顺序：基础工具(auth/animation/theme/globals/sound) → UI 组件(toast/confirm/tabs/categories)
#          → 业务模块(transfer/log/schedule/history/settings/tmdb/search/dashboard) → 入口(init)
JS_FILES=(
  auth animation theme globals sound toast confirm tabs categories
  transfer log schedule history settings tmdb search dashboard init
)
: > "$JS_TMP"
for f in "${JS_FILES[@]}"; do
  p="$SRC_DIR/scripts/$f.js"
  if [ ! -f "$p" ]; then
    echo "错误: 缺少前端模块 $p" >&2
    exit 1
  fi
  echo "// ===== module: $f.js =====" >> "$JS_TMP"
  cat "$p" >> "$JS_TMP"
  echo "" >> "$JS_TMP"
done
JS_CONTENT=$(minify "$JS_TMP" js)

# 构建指纹：记录对应提交 sha，便于回溯产物版本（不含时间戳，避免每次构建产生无意义 diff）
# 注意：git 调用可能因运行环境（CI/沙箱/无仓库）返回非 0；加 `|| true` 兜底，
# 避免 `set -e` 在赋值语句处直接中断构建导致产物未生成。
_git_sha=$(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || true)
if [ -z "$_git_sha" ]; then
  _git_sha=$(git rev-parse --short HEAD 2>/dev/null || true)
fi
FINGERPRINT="sha:${_git_sha:-unknown}"

# 读取 HTML body
BODY_CONTENT=$(cat "$SRC_DIR/body.html")

# 组装最终 HTML
cat > "$DIST_FILE" << 'BUILD_EOF'
<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>豆瓣自动转存</title>
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<script>
/* FOUC 防闪烁：在 CSS 之前同步设置主题 */
(function(){var t=localStorage.getItem('theme')||'dark';document.documentElement.setAttribute('data-theme',t)})();
</script>
<style>
BUILD_EOF

echo "$CSS_CONTENT" >> "$DIST_FILE"

cat >> "$DIST_FILE" << 'BUILD_EOF'
</style>
</head>
<body>
BUILD_EOF

echo "$BODY_CONTENT" >> "$DIST_FILE"

cat >> "$DIST_FILE" << BUILD_EOF

<script>
// ===== DBAuto frontend bundle =====
// $FINGERPRINT
// 由 build.sh 拼接生成（模块化源文件合并为单文件）。
// 注意：不使用 IIFE 包裹。页面大量使用内联 onclick/onchange 事件处理器，
// 这些处理器在全局作用域查找被调用的函数；若包进 IIFE 会导致函数不可见、按钮点击失效。
// 压缩（esbuild --minify）仅折叠局部变量与空白，保留顶层全局函数名，故内联处理器不受影响。
BUILD_EOF

echo "$JS_CONTENT" >> "$DIST_FILE"

cat >> "$DIST_FILE" << 'BUILD_EOF'
</script>
</body>
</html>
BUILD_EOF

echo "Built: $DIST_FILE"
echo "Size: $(wc -c < "$DIST_FILE") bytes, $(wc -l < "$DIST_FILE") lines"
