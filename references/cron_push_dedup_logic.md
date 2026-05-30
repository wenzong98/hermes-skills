# 定时推送去重逻辑（工作日 9:00）

## 目标
将美股 ETF 建议改为 **每个工作日早上 9:00** 运行，但不是每次都推送；只有当“今日建议”相对前一次已记录建议发生变化时才推送。

## 当前实现
- cron schedule: `0 9 * * 1-5`
- script: `~/.hermes/scripts/us_etf_current_advice_push.sh`
- state file: `~/.hermes/skills/research/us-etf-quant-system/references/cron_run/.advice_state.json`

## 去重字段
当前建议是否变化，按以下字段比较：
- `decision.action_label`
- `decision.dca_multiplier`
- `decision.new_buy_spy_weight_pct`
- `decision.new_buy_qqq_weight_pct`
- `decision.trim_signal_qqq_pct_now`
- `diagnosis.regime`

如果以上字段与上次保存状态完全一致，则判定为“建议未变化”，脚本输出：
- `⏭️ 建议内容与昨日相同，跳过推送`

## 注意：市场涨跌 ≠ 建议变化
即使 SPY / QQQ 当日涨幅有变化，只要建议字段没变，也应跳过推送。

因此：
- **SPY/QQQ 当日涨幅用于展示市场状态**
- **不作为是否推送的判断字段**

## 当日涨幅展示
在 `current_market_advice.py` 中新增：
- `market.spy_daily_return_pct`
- `market.qqq_daily_return_pct`

定义：最新交易日收盘价相对前一交易日收盘价的百分比变化。

报告顶部建议保留类似格式：
- `市场日期：2026-05-28 | SPY 今日涨幅：+0.55% | QQQ 今日涨幅：+0.84%`

## 关键坑
### 1. 手动执行脚本不会自动把消息发到用户
直接运行：
- `bash ~/.hermes/scripts/us_etf_current_advice_push.sh`

只会：
- 生成 JSON / Markdown 输出
- 在 stdout 打印文本

**不会自动发到 Telegram / home channel。**

真正的投递路径有两种：
1. 由 cron job 执行，并依赖 cron job 的 `deliver` 字段投递
2. 在当前会话里显式调用 `send_message`

### 2. `deliver: origin` 的含义
`origin` = 回到当前会话来源（例如当前 Telegram 对话），不是广播到所谓“home channel”。

如果用户问“有没有真的推送到我这里”，要检查的是：
- 是不是只在 shell 里跑了脚本
- 有没有真正经过 cron deliver 或 `send_message`

## 推荐验证步骤
1. 运行脚本一次，确认：
   - `references/cron_run/current_market_advice.json` 已更新
   - `references/cron_run/.advice_state.json` 已写入
2. 立刻再运行一次，若建议未变，应输出“跳过推送”
3. 检查 cron job：
   - schedule = `0 9 * * 1-5`
   - deliver = `origin`（或用户指定目标）
4. 若用户要求“现在推送一次给我”，不要只跑脚本；应额外显式发送消息
