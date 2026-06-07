# 定时推送逻辑（周一至周六 9:00）

## 当前实现
- cron schedule: `0 9 * * 1-6`
- 周六任务用于处理中国时间周六早晨才完成的美股周五收盘数据。
- script: `~/.hermes/scripts/us_etf_current_advice_push.py`（Python，非 bash）
- state file: `~/.hermes/skills/research/us-etf-quant-system/references/cron_run/.advice_state.json`

## 投递路径
1. **cron job 自动投递**：`us-etf-daily-market-advice` (job_id: `0796e6f17c67`)，`deliver: telegram:8208820975`。cron scheduler 捕获脚本 stdout 内容并投递。
2. **手动投递**：在当前会话里调用 `send_message` 直接发到 `telegram:8208820975`。

直接运行脚本（`python us_etf_current_advice_push.py`）只会把内容打印到 stdout，**不会自动发到 Telegram**。

## 去重逻辑（已注释，2026-06-03 起停用）
原 dedupe 逻辑会比较 `action_label / dca_multiplier / spy_weight_pct / qqq_weight_pct / trim_signal_pct / regime` 与上次状态，完全一致时跳过推送。

2026-06-03 因用户要求"每次都推送"（即使建议未变），dedupe 逻辑已在 `us_etf_current_advice_push.py` 中注释掉，脚本每次都输出完整推送内容。

## 手动强制推送
如 cron 未触发但需要即时推送，直接在会话中调用 `send_message`，推送文本从脚本 stdout 复制。

## 关键坑
### 1. 调试 cron 失败时先看完整 stack
cron 报 `ModuleNotFoundError: pandas` 可能是假象（手滑测试子脚本的 PATH 问题）。真正的失败 stack 在 `~/.hermes/cron/output/<job_id>/<date>.md`。详见 `hermes-cron-no-agent-subprocess-pitfall` skill 附2。

### 2. push 包装脚本的 `cmd[:-2]` 定时炸弹
`us_etf_current_advice_push.py` 复用 `strict_cmd[:-2]` 构建第二个 subprocess 调用时，恰好切掉了 `--cape-vintage-path <path>` 这一对 flag，导致 `--require-adjusted` 模式下 `prepare_dataset` 抛 RuntimeError。修复：第二个调用完整写出所有 flag，不切片。详见 `us-etf-quant-system` SKILL.md Common Pitfalls #11。
