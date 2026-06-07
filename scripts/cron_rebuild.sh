#!/usr/bin/env bash
# ----------------------------------------------------------------
# DEPRECATED 2026-06-08: 不要再加到 crontab / launchd
# ----------------------------------------------------------------
# 原意图:  每个交易日 22:00 (美股收盘) 重新 build + reload dashboard
# 旧部署:  把这行加到 crontab (`crontab -e`)
#
#   0 22 * * 1-5 /Users/bytedance/.hermes/skills/research/us-etf-quant-system/scripts/cron_rebuild.sh >> /tmp/dashboard-cron.log 2>&1
#
# 实际情况 (2026-06-08 审计):
#   * 系统 crontab -l 不包含此行
#   * launchd 没有 cron_rebuild 标签的 plist
#   * 05:30 launchd 跑的 com.hermes.usetf-daily-refresh.sh
#     step 1 (run_daily_pipeline.py) 已经覆盖 advice + backtest +
#     dashboard build,等价于此脚本的步骤 1+2。
#   * 22:00 launchd 实际跑的是 usetf-weekly-backtest,跟 dashboard 无关
#
# 留此脚本只供以下场景使用:
#   * 美股盘中临时手动 rebuild(例如手动修完数据想立刻看效果)
#   * 调试 launchd 行为时的故障重现入口
#
# 如果确实需要"美股收盘后当晚看到新数据",可以新建一个
# ~/Library/LaunchAgents/com.hermes.usetf-dashboard-rebuild.plist
# 在 22:00 工作日跑此脚本,但请先确认 daily-refresh 不会双跑。
# ----------------------------------------------------------------
set -euo pipefail

ROOT="/Users/bytedance/.hermes/skills/research/us-etf-quant-system"
LOG="/tmp/dashboard-cron.log"
TS="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

cd "$ROOT"
echo "=== [$TS] cron rebuild started ===" >> "$LOG"

# 1. 拉新 advice (current_market_advice)
python scripts/current_market_advice.py >> "$LOG" 2>&1 || echo "  ⚠️ advice update failed" >> "$LOG"

# 2. 重新 build dashboard
python scripts/build_dashboard.py >> "$LOG" 2>&1 || echo "  ❌ build failed" >> "$LOG"

# 3. (可选) 推送到 GitHub Pages / CDN
# git -C "$ROOT" add references/dashboard && git commit -m "auto: dashboard rebuild $TS" && git push

# 4. (可选) 发推送通知
# python scripts/notify.py "Dashboard 已更新 $(date '+%F')" >> "$LOG" 2>&1

echo "=== [$TS] cron rebuild done ===" >> "$LOG"
