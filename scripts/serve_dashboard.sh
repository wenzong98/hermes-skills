#!/usr/bin/env bash
# 启动 dashboard 静态服务
# 用法: ./scripts/serve_dashboard.sh [port]
# 默认端口 9876
set -euo pipefail

PORT="${1:-9876}"
DASHBOARD_DIR="$(cd "$(dirname "$0")/.." && pwd)/references/dashboard"

if [ ! -d "$DASHBOARD_DIR" ]; then
  echo "❌ 找不到 $DASHBOARD_DIR，请先跑 build_dashboard.py 生成"
  exit 1
fi

cd "$DASHBOARD_DIR"
echo "🌐 启动 dashboard 服务: http://localhost:$PORT/index.html"
echo "   按 Ctrl+C 停止"
exec python3 -m http.server "$PORT"
