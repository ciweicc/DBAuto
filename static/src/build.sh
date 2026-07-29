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

# 读取 JS（按依赖顺序）
JS_CONTENT=$(cat \
  "$SRC_DIR/scripts/auth.js" \
  "$SRC_DIR/scripts/animation.js" \
  "$SRC_DIR/scripts/theme.js" \
  "$SRC_DIR/scripts/globals.js" \
  "$SRC_DIR/scripts/toast.js" \
  "$SRC_DIR/scripts/confirm.js" \
  "$SRC_DIR/scripts/tabs.js" \
  "$SRC_DIR/scripts/categories.js" \
  "$SRC_DIR/scripts/transfer.js" \
  "$SRC_DIR/scripts/log.js" \
  "$SRC_DIR/scripts/schedule.js" \
  "$SRC_DIR/scripts/history.js" \
  "$SRC_DIR/scripts/settings.js" \
  "$SRC_DIR/scripts/tmdb.js" \
  "$SRC_DIR/scripts/search.js" \
  "$SRC_DIR/scripts/dashboard.js" \
  "$SRC_DIR/scripts/init.js" \
)

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

cat >> "$DIST_FILE" << 'BUILD_EOF'

<script>
BUILD_EOF

echo "$JS_CONTENT" >> "$DIST_FILE"

cat >> "$DIST_FILE" << 'BUILD_EOF'
</script>
</body>
</html>
BUILD_EOF

echo "Built: $DIST_FILE"
echo "Size: $(wc -c < "$DIST_FILE") bytes, $(wc -l < "$DIST_FILE") lines"
