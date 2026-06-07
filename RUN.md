# RUN — 启动 / 端口 / 健康检查

> 量化看版 `us-etf-quant-system` 的运维速查表。
> 5 章: 端口表 / launchd 任务 / 手动命令 / 环境变量 / 健康检查。
> 故障排查顺序: **数据源 → API 契约 → 启动时序 → 图表 → 翻译**。

---

## 1. 端口表

| 端口 | 用途 | 启动方式 | 状态 |
|---|---|---|---|
| **8766** | launchd 托管的 dashboard HTTP server | `com.hermes.usetf-dashboard-server` (KeepAlive) | 长期运行,失败自动重启 |
| **9876** | 手动临时 rebuild 后的预览 | `bash scripts/serve_dashboard.sh 9876` | 一次性,Ctrl+C 退出 |

> ⚠️ 不要同时跑两个端口。如果手动改完模板要预览,**先停 launchd**:
> ```bash
> launchctl bootout gui/$(id -u)/com.hermes.usetf-dashboard-server
> bash scripts/serve_dashboard.sh 9876
> # 看完之后
> launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.usetf-dashboard-server.plist
> ```

---

## 2. launchd 任务清单

所有 plist 都在 `~/Library/LaunchAgents/`。系统级 `crontab -l` 与本项目**无关**(只有 hermes 自身的 backup 任务)。

| Label | 触发 | 跑的 wrapper | 日志 |
|---|---|---|---|
| `com.hermes.usetf-daily-refresh` | 周一至周六 05:30 | `~/.hermes/scripts/usetf-daily-refresh.sh` | `~/.hermes/cron/usetf-daily-refresh.log` |
| `com.hermes.usetf-weekly-backtest` | 周日 22:00 | `~/.hermes/scripts/usetf-weekly-backtest.sh` | `~/.hermes/cron/usetf-weekly-backtest.log` |
| `com.hermes.usetf-monthly-matrix` | 每月 1 日 22:00 | `~/.hermes/scripts/usetf-monthly-matrix.sh` | `~/.hermes/cron/usetf-monthly-matrix.log` |
| `com.hermes.usetf-quarterly-backtest` | 1/4/7/10 月 5 日 22:00 | `~/.hermes/scripts/usetf-quarterly-backtest.sh` | `~/.hermes/cron/usetf-quarterly-backtest.log` |
| `com.hermes.cape-monthly-snapshot` | 每月 5 日 09:00 | `~/.hermes/scripts/cape-monthly-snapshot.sh` | `~/.hermes/cron/cape-monthly-snapshot.log` |
| `com.hermes.usetf-dashboard-server` | RunAtLoad + KeepAlive | `~/.hermes/scripts/usetf-dashboard-server.sh` | `~/.hermes/cron/usetf-dashboard-server.log` |

`usetf-daily-refresh.sh` 内部三步:
1. `python3 scripts/run_daily_pipeline.py` — 数据 → 建议 → 回测 → dashboard
2. `bash ~/.hermes/scripts/us_etf_llm_copilot.sh` — LLM 副驾驶 v1 + v2
3. `python3 ~/.hermes/scripts/us_etf_current_advice_push.py` — Telegram 推送

**手动 kickstart** (调试 / 重跑):
```bash
launchctl kickstart -k gui/$(id -u)/com.hermes.usetf-daily-refresh
launchctl kickstart -k gui/$(id -u)/com.hermes.cape-monthly-snapshot
```

**查看状态**:
```bash
launchctl list | grep hermes
```

---

## 3. 手动命令

| 目的 | 命令 |
|---|---|
| 跑今日完整流水线 (debug) | `python3 scripts/run_daily_pipeline.py --python $(which python3)` |
| 单跑 advice | `python3 scripts/current_market_advice.py` |
| 重新 build dashboard | `python3 scripts/build_dashboard.py --no-open` |
| 跑 3 年回测 | `python3 run_backtest.py` 或 `python3 scripts/backtest_us_etf.py` |
| 推送 Telegram | `US_ETF_FORCE_PUSH=1 python3 ~/.hermes/scripts/us_etf_current_advice_push.py` |
| 手动 rebuild 并预览 | 见 §1 的"先停 launchd"流程 |
| 跑 LLM 副驾驶 | `python3 scripts/llm_copilot.py` |
| 跑全量回归 | `python3 scripts/run_full_regression.py` |
| 看持久化决策 | `sqlite3 data/decisions.db "SELECT * FROM decision_snapshots ORDER BY market_date DESC LIMIT 5"` |
| 看模拟成交 | `python3 scripts/trade_ledger.py list` |

> ⚠️ `scripts/cron_rebuild.sh` 已 **DEPRECATED**(头部 banner 说明),不要加到 crontab / launchd。launchd `daily-refresh` step 1 已经覆盖此流程。

---

## 4. 环境变量

复制 `.env.example` 为 `.env` 并填入真实 key。`.env` 已在 `.gitignore` 内,**绝不**入库。

```bash
# LLM 副驾驶 (anthropic 兼容端点)
LLM_API_KEY=sk-cp-...
LLM_BASE_URL=https://api.minimaxi.com/anthropic
LLM_MODEL=MiniMax-M3
LLM_BACKEND=anthropic

# 新闻 / 搜索
TAVILY_API_KEY=tvly-dev-...
NEWSAPI_KEY=...
CURRENTS_API_KEY=...    # 当前 .env 有 key 但代码未使用,先放着

# 本地代理(部分数据源需要)
HTTPS_PROXY=http://127.0.0.1:4780
HTTP_PROXY=http://127.0.0.1:4780
ALL_PROXY=http://127.0.0.1:4780
```

未配置时:
- LLM 副驾驶自动降级到 offline 模式(只读 `references/llm_usage.jsonl` 历史)
- NewsAPI / Tavily 失败时 dashboard 模块降级显示"暂无数据"

---

## 5. 健康检查

```bash
# 1. launchd 任务都在跑?
launchctl list | grep -E "hermes.*usetf|hermes.*cape"

# 2. 今日 daily refresh 跑过没?
tail -20 ~/.hermes/cron/usetf-daily-refresh.log
# 期望最后一行: "US ETF daily refresh done" + Telegram 推送内容

# 3. 今日 pipeline 写过 daily_log 没?
ls -la daily_log/ | tail -3
# 期望有当日 .txt (周末例外)

# 4. 关键数据库 mtime 是近期?
ls -la data/*.db
# prices.db / factors.db / decisions.db / trades.db

# 5. dashboard 可访问?
curl -sI http://localhost:8766/index.html | head -1
# 期望 HTTP/1.0 200 OK

# 6. 9 点 push (hermes 内部 cron) 跑过没?
ls -la ~/.hermes/cron/output/0796e6f17c67/ | tail -3
# 期望有当日 .md

# 7. cape monthly snapshot 上次成功没?
tail -10 ~/.hermes/cron/cape-monthly-snapshot.log
# 期望最近一次 "[step 2/3] commit OK" + "[step 3/3] pushing summary"
```

### 故障排查固定顺序

按 memory `quant-recent-issues-summary` 的 8 步验收:

```
数据源 → API 契约 → 启动时序 → 图表渲染 → 翻译回调
```

不要按用户描述的顺序修,因为用户描述的常常是"症状"而非"根因"。

### 5 类高频问题(2026-06-06~07 已爆发)

1. **数据源空跑** — GDELT / ForexFactory / NewsAPI 静默失败
2. **模块消失** — dashboard 启动时序问题
3. **图表布局崩坏** — echarts canvas 没 ResizeObserver
4. **翻译漏跑** — LLM 翻译只跑主流程,数据刷新后未回译
5. **启动混乱** — 端口/IP/命令散落(此文件即是修复)

详见 `~/.claude/projects/-Users-bytedance--hermes-skills-research/memory/quant-dashboard-fixes.md`。

---

## 附:不在本表内的"提示性"命令

| 想看 | 命令 |
|---|---|
| 本次 LLM 调用花了多少钱 / token | `tail -20 references/llm_usage.jsonl \| jq .` |
| CAPE 估值表最新观测月 | `tail -3 references/cape_vintage.csv` |
| 7 日回测摘要 | `cat references/daily_log/\$(date +%F).txt 2>/dev/null` |
| 投资组合当前建议(中文) | `cat references/cron_run/current_market_advice.md` |
| GitHub CI 状态 | `gh pr checks` 或在 PR 页面看 `xss-discipline` job |
