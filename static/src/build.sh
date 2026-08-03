#!/bin/bash
# DBAuto 前端构建脚本
# 将模块化的 CSS/JS 文件合并为单个 index_new.html
# 使用方法: bash build.sh

set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_FILE="$SRC_DIR/../index_new.html"

echo "Building DBAuto frontend..."

# 读取 CSS
CSS_CONTENT=$(cat "$SRC_DIR/styles/tokens.css" "$SRC_DIR/styles/main.css" 2>/dev/null || cat "$SRC_DIR/styles/main.css")

# 读取 JS（按依赖顺序，顺序不可随意调整）
# 依赖顺序：基础工具(auth/animation/theme/globals/sound) → UI 组件(toast/confirm/tabs/categories)
#          → 业务模块(transfer/log/schedule/history/settings/tmdb/search/dashboard) → 入口(init)
JS_FILES=(
  auth animation theme globals sound toast confirm tabs categories
  transfer log schedule history settings tmdb search dashboard init
)

# 构建指纹：记录对应提交 sha，便于回溯产物版本（不含时间戳，避免每次构建产生无意义 diff）
_git_sha=$(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null)
if [ -z "$_git_sha" ]; then
  _git_sha=$(git rev-parse --short HEAD 2>/dev/null)
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
// 由 build.sh 拼接生成。整体包进 IIFE：各模块共享同一作用域、互不污染 window 全局。
// 注：此处有意不加 'use strict'，避免对隐式全局变量的依赖在无前端测试覆盖时引发运行时报错；
//     待补充前端测试后再行启用。
(function(){
BUILD_EOF

for f in "${JS_FILES[@]}"; do
  p="$SRC_DIR/scripts/$f.js"
  if [ ! -f "$p" ]; then
    echo "错误: 缺少前端模块 $p" >&2
    exit 1
  fi
  echo "// ===== module: $f.js =====" >> "$DIST_FILE"
  cat "$p" >> "$DIST_FILE"
  echo "" >> "$DIST_FILE"
done

cat >> "$DIST_FILE" << 'BUILD_EOF'
})();
</script>
</body>
</html>
BUILD_EOF

echo "Built: $DIST_FILE"
echo "Size: $(wc -c < "$DIST_FILE") bytes, $(wc -l < "$DIST_FILE") lines"
