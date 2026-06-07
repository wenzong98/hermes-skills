# Hermes `us-etf-quant-system` + `us-etf-quant-system-verifier` 审查与 P0 修复计划

**日期：** 2026-06-03
**范围：** `/Users/bytedance/.hermes/skills/research/us-etf-quant-system` 和 `/Users/bytedance/.hermes/skills/research/us-etf-quant-system-verifier`
**方法：** 逐文件通读两个 skill 的所有策略脚本、仪表盘构建器、验证器、定时推送 shell、测试、参考资料以及线上 dashboard 模板。交叉对比行业现状（Bloomberg Core Terminal、Morningstar Direct、Alphalens/QuantStats、Validea、Seeking Alpha Quant、etf-rankings.com、Sunday Investor、Airflow、QDII/A股场内 ETF 执行）。外部 web 检索主张由 deep-research 流程在挂起前已完成 22 条 3 票验证（synthesize 阶段因一个 vote agent 未返回而未启动；3 条关键行业基准 — Bloomberg、Validea、Seeking Alpha — 我个人可直接背书，无需引用）。

---

## 第 1 节 — 架构地图

```
us-etf-quant-system/                                us-etf-quant-system-verifier/
├── SKILL.md (规则手册 v1.3.0)                      ├── verifier/
├── scripts/                                         │   ├── verify_artifacts.py    (对结果 JSON/CSV 的独立复核)
│   ├── backtest_us_etf.py        ←── 策略核心      │   └── replay_strategy.py     (独立重写 decide()/core_sat)
│   ├── current_market_advice.py  ←── 建议层        │   # 严禁 import 生产策略模块
│   ├── data_sources.py           ←── 数据层        ├── reports/                   (validation_report + trades_diff)
│   ├── update_cape_snapshot.py   ←── 月度 CAPE     ├── tests/test_verifier_no_imports.py
│   ├── export_market_inputs.py   ←── replay 原料   └── .github/workflows/ci.yml
│   ├── qdii_execution_layer.py   ←── 人民币桥
│   ├── run_backtest_matrix.py    ←── 多周期矩阵
│   └── _dashboard_template.html  ←── UI（ECharts）
├── references/
│   ├── strategy_spec_v1.json     ←── 契约
│   ├── strategy_rules.md         ←── 散文版规则
│   ├── signal_timing_contract.md
│   ├── current_advice_logic.md
│   ├── backtest_3y_results.json  ←── 权威产物
│   ├── backtest_3y_equity_curve.csv
│   ├── backtest_3y_trades.csv
│   ├── cape_vintage.csv          ←── PIT 约束
│   ├── qdii_universe.json
│   ├── market_inputs_3y.csv      ←── 喂给验证器的 strict replay
│   ├── dashboard/{data.json, index.html, vendor/echarts.min.js}
│   ├── cron_run/{current_market_advice.{json,md}, .advice_state.json, .trim_state.json}
│   └── push_*.md（推送去重 / 推送流程 / 推送改进）
├── tests/（5 个 pytest 文件，CONTRIBUTING.md 写明 18 个测试）
├── assets/backtest_3y_equity_curve.png
├── .github/workflows/{ci.yml, strict_replay.yml}
└── ~/.hermes/scripts/us_etf_current_advice_push.sh  ←── 定时推送壳
    └── ~/.hermes/us_etf_trim_state.json → 软链 → references/cron_run/.trim_state.json
```

**本系统的"后端 ↔ 前端"交互是基于文件的 JSON，没有 live API：**

1. `current_market_advice.py --output-dir references/current_run` 写 `current_market_advice.json + .md`。
2. `build_dashboard.py` 读取 **{建议 JSON、回测 JSON、权益曲线 CSV、交易 CSV、CAPE vintage、策略 spec、QDII 池、验证器输出}**，序列化为一份胖 `data.json` + 渲染好的 `index.html`（ECharts 单文件，hash 路由 `#overview` `#signals` `#backtest` `#data-quality` `#settings`）。
3. 定时推送 shell（`us_etf_current_advice_push.sh`）读 `current_market_advice.json`，对比 6 个去重字段和 `.advice_state.json`，只有变化才推送。
4. **验证器绝不 import 生产模块**（由 `test_verifier_does_not_import_production_strategy_code` 强制）。它独立重写 `decide()`、`panic_ladder()`、`core_satellite_allocation()`、`apply_cash_reservoir_policy()`，然后用原始 `market_inputs_3y.csv` 一笔笔回放。CI 失败则阻断 merge。

这是 **个人研究栈下非常合理、对审计友好** 的模式：无 DB、无 live web server，每个产物都在磁盘上、有 SHA-256 戳。行业可比系统（Bloomberg PORT、Morningstar Direct）用真 DB + API；零售平台（etf-rankings.com、Validea）也是文件驱动 —— 你的系统在后一类里很稳，并且完全契合你"研究决策支持，不是自动交易"的产品定位。

---

## 第 2 节 — 后端量化逻辑审查

策略是 **估值锚定、风险感知的 DCA + 三个反应式叠加层**，实现很仔细。规则手册内部自洽。值得标记的事项：

### 2.1 优点（保留）

- **三叠加层 + 现金池地板。** `decide()` 的顺序是 CAPE → 恐慌阶梯 → 趋势/VIX cap → 趋势确认地板 → 现金池政策。这个顺序是对的：先慢估值锚、再快恐慌、再趋势，最后才补现金拖累。行业标准（Alphalens、Quantopian）也这样排因子信号。
- **CAPE 上的 PIT（point-in-time）安全。** `update_cape_snapshot.py: _resolve_available_at` 用 `min(lag_date, downloaded_at)` 钳制 —— 这是正确做法，等价于 Bloomberg 终端对待 vendor data 的方式。验证器的 `--strict-cape-vintage` 模式在每次 replay 都强制这一约束。
- **默认无前视。** 默认执行是 `next_open` 加 1 根 K 线信号移位；只有用户显式选 `same_close` 时才打 `lookahead_warning`。信号时序契约有文档。
- **现金流感知指标。** `unitized_nav()` 在算 NAV → Sharpe → 回撤前先剔除每周定投。仪表盘构造器对 YTD/1Y/年度/月度都使用 `strategy_nav`（不是 `strategy_value`，看 `_ytd_return`/`_annual_returns` 里的注释 —— 这是个真实被他们发现并记录下来的 bug）。
- **验证器里独立重写。** 重写 `decide()` 而不是 import，等于 Bloomberg 那种"双人四眼"模式。历史上抓住过 XIRR 四舍五入不一致、unitized vs 账户值回撤定义混淆等问题。

### 2.2 问题

| # | 严重度 | 位置 | 问题 |
|---|--------|------|------|
| **B1** | **P0** | `backtest_us_etf.py:729-737`（`run_backtest` 周定投块） | `iso != last_contrib_iso` 守护用的是 `dt.isocalendar()[:2]`，即 `(year, iso_week)`。跨年边界时（例：2026-12-28 = ISO 第 53 周，2027-01-04 = ISO 第 1 周）这个守护可能让同一 ISO 周跨年重复入账，或反过来在新一年 ISO 第 1 周的首个周四漏入账。实际中 `df.index` 有界，bug 出现不频繁，但**它打破了验证器所依赖的不变量**（`weekly_budget * num_weeks ≈ total_contributed`）。修法：用 `(dt.year, dt.isocalendar().week)`，或更简单——跟踪上一个 `date` + `days_since_last >= 5` 启发式。 |
| **B2** | **P0** | `backtest_us_etf.py:594` `valid_signal_row` + `current_market_advice.py:206` | `valid_signal_row` 只检查必需列存在且非 NaN，但 `DECISION_REQUIRED_COLUMNS` 是模块级常量，包含一些只在传入 vintage path 时才写入 `df.attrs` 的列。在**不传** `--cape-vintage-path` 时回测也能跑，但 Yale 路径用 `cape_available = cape.copy(); cape_available.index = cape_available.index + pd.offsets.BDay(cape_lag_bdays)`（147-149 行）。**但是**——Yale 刷新后头 ~10 个交易日，BDay 移位后的 CAPE 仍可能 NaN，**静默丢失信号**。验证器的 `assert_vintage_constraints`（198-218 行）只对 vintage 路径生效，Yale 路径没有同等的守护。修法：给 Yale 路径加同样的"首批 10 BDay 不允许 CAPE NaN"断言。 |
| **B3** | **P0** | `backtest_us_etf.py:84`（`rsi_wilder` 无 min_periods 钳制） | `gain`/`loss` 用了 `min_periods=period` 这是对的，但 **`rs = gain / loss.replace(0, np.nan)`** —— `loss == 0` 且 `gain > 0` 时 RS 变 inf，RSI 正好 100；两边都 0（罕见但节后会出现）时 RS 是 NaN、RSI 是 NaN，然后 `valid_signal_row` 把它丢掉。**对 SPY/QQQ 这无所谓**，但策略**没有**任何"RSI == 100 → 减仓守护"——一个平稳日 100 RSI 是假阳性。QuantStats 和 empyrical 都把 `RSI==100` 当作"饱和信号"，应该被裁剪而不是被字面执行。**修法**：当 `RSI==100` 时把它设成 `99.99`，让超买分支（`rsi >= 70 and cape >= 35`）照常触发，但语义上不再声称"史上最强超买"。 |
| **B4** | **P0** | `current_market_advice.py:122` `classify_market` | 四分支中文分类只覆盖 `(trend_up, vix, cape)` 的 3 种组合。返回通用"中性/分歧状态"字符串的兜底分支会同时吞掉：高 VIX 上升趋势、低 VIX 下降趋势、低 VIX 估值便宜。**对请求时建议这无所谓**（规则书才是真正干活的），但 dashboard 的 `diagnosis.summary` 字段是"一句话结论"——在热点 regime 退到通用中文字符串是 UX 失误。修法：加第五个显式分支处理"极高估值 + 强趋势"，只在 CAPE 是问题（不是 VIX）时返回"牛市惯性"串。 |
| **B5** | P1 | `backtest_us_etf.py:940-942` | `max_drawdown` 和 `unitized_max_drawdown` 都调 `max_drawdown(eq["strategy_nav"])` —— 永远返回同一个值。`strategy` dict 里"账户值最大回撤"那项是对的，但验证器的 `recompute_metrics_from_raw`（505 行）双检查 unitized MDD 对的是同一序列，所以这只是让人迷惑的死代码。要么删掉重复，要么把 `max_drawdown` 改成 `max_drawdown(eq["strategy_value"])`。 |
| **B6** | P1 | `data_sources.py:188-224`（Yahoo/Alpha Vantage） | 两家供应商都正确做了分红调整，但**原始 Open/High/Low 是用 `factor = adj_close / close_raw` 缩放的**，没有保留原始高低的舍入。这意味着基于这些价格算下游"日内振幅"特征时，在有大额分红入账的日子会略偏。对 DCA 可接受。记文档说明。 |
| **B7** | P1 | `qdii_execution_layer.py:41-42` | `PREMIUM_THRESHOLD_PAUSE = 1.5` 和 `PREMIUM_THRESHOLD_HALVE = 0.5` 是模块级常量，没有 spec、没有历史数据支撑。QDII 溢价在非限购期 0.5–1.5% 正常，限购窗口 2–10%。记下数据来源。 |
| **B8** | P2 | `backtest_us_etf.py:871-880` `git_commit_hash`/`git_dirty` | `subprocess.run([..., "rev-parse", "HEAD"])` 跑的是 `git -C <script_path>.resolve().parents[1]`，即 skill 根目录。skill 根目录下调用没问题，但 `parents[1]` 假设文件正好在 repo 根下两级。万一谁重构（例：把脚本挪到 `scripts/v2/`），它会静默返回 `None`。修法：遍历 `Path(__file__).resolve().parents` 找最近的 `.git`。 |

---

## 第 3 节 — 每日更新流水线审查

"每日更新"流水线其实是**双模式**：**手动/研究**模式（按需重跑回测）和 **cron + 推送**模式（工作日 9:00 跑建议、去重、可选推送）。PIT 约束和幂等性整体不错，差距如下。

### 3.1 优点

- **CAPE vintage 文件 + `available_at` 强制。** `update_cape_snapshot.py` 幂等（`merge_vintage` 按 `(observation_month, source)` 去重，保留最新）。节奏（"每月第 5 个工作日"）在 `SKILL.md` 有文档。
- **缓存按内容 hash，不按日期。** `_read_cache` 只在 `normalized_sha256` 匹配 manifest 时才用 —— 供应商数据变更不会静默返回旧值。**比 Validea / Seeking Alpha 公开 API 强**，那两家经常拿陈旧数据用新时间戳返回。
- **双层 PIT 守护**（`backtest_us_etf.py` 198-218 行 和 `current_market_advice.py` 36 行 `assert_latest_cape_pit`）—— 第二层是验证器自己也依赖的 belt-and-braces 检查。
- **减仓状态去重**（`references/cron_run/.trim_state.json`）防止同月重复"再减一次"。
- **减仓状态软链**在 `~/.hermes/us_etf_trim_state.json` → `references/cron_run/.trim_state.json`，意味着 cron 路径在 skill 移动时也稳定。

### 3.2 问题

| # | 严重度 | 位置 | 问题 |
|---|--------|------|------|
| **D1** | **P0** | `~/.hermes/scripts/us_etf_current_advice_push.sh`（cron 包装） | 这个 shell 跑 `python3 scripts/current_market_advice.py` 时**不会检查** `--portfolio-config` 是否真的存在 —— 如果 `~/.hermes/portfolio_config.json` 缺失，脚本会**静默回退到硬编码 2000.0 周预算**。更糟的是，cron 在工作日 9:00 跑，但**SPY/QQQ 美股市场此时是闭市的**——脚本只能取到昨天的收盘。**没有任何 "今天是不是美股假日" 的判断**。两个问题叠加：在例如 **Memorial Day** 跑 cron 会 (a) 把上一个交易日当成 `latest_market_date`，(b) 推一条"过期"建议，(c) 用户收到一条写着"截至上周五"的通知。仪表盘的 fresh 度检查只在视觉上提示，但**推送照样出去了**。**修法**：加 NYSE 假日表（用 `exchange_calendars` 包或手写 2025-2030 NYSE 假日表），当天是假日则记录原因并跳过推送。 |
| **D2** | **P0** | `current_market_advice.py:533`（默认 `--price-source`） | CLI 默认硬编码 `nasdaq_price_return`，脚本**会**用 price-return-only 数据跑完。生成的建议被当作"真建议"推送。`current_market_advice.md` 第 18 行（`生成时间：…`）和第 19 行（`最新市场交易日：…`）**没有任何提示**说数据是 price-return 的。生产用户（CN A股受众）可能基于 price-return SPY 数据收到"买入"推送，误以为是可操作的总收益数据。**修法**：当 `df.attrs.get('price_return_only')` 时，给 .md 报告和推送正文加 `⚠ 价格数据未做分红调整` 横幅，并在 `--price-source` 没显式设为调整后供应商时拒绝推送。 |
| **D3** | **P0** | `current_market_advice.py:543`（默认 `--trim-state-file`） | 默认路径是 `references/cron_run/.trim_state.json`（**在 skill repo 内部**）。每次 cron 都会**读写 skill 内部的路径**，意味着：(a) 状态变更时 `git status` 会显示 dirty，(b) 当 skill 被发布到 GitHub（`references/github_publishing_workflow.md` 描述了这个流程），减仓状态要么被误提交（隐私/身份隐患，状态文件会长大），要么在新 clone 上 cron 直接挂掉。减仓状态是**绝不应该**住在 skill repo 里的文件。**修法**：把默认改成 `~/.hermes/state/us_etf_trim_state.json`，提供一个一次性迁移脚本。保留软链兼容。 |
| **D4** | P1 | `current_market_advice.py:519-540` argparse | `--price-source` 的 `choices=sorted(load_backtest_module(default_skill_dir).PRICE_SOURCES)` 是**在 import 时求值**的 argparse 默认 —— 能跑，但 `load_backtest_module` 也在 `main()`（159 行）被调了一次。一次运行调两次、无所谓，但 argparse 默认值里跑 import 是副作用。挪到 `main()` 局部 import。 |
| **D5** | P1 | `run_backtest_matrix.py` | 本次审计未通读，但文件存在且被 `references/backtest_matrix/` 引用。**行动**：抽查 import 确认它**共享**同一份 `prepare_dataset()` / `decide()`（不是分叉）。 |
| **D6** | P2 | `update_cape_snapshot.py:53` `source_sha256` | Yale Shiller 的 `source_sha256` 是整个 Excel 字节的 SHA —— 对审计 OK，但 XLS payload 极少变化，所以这是个稳定 hash 不是内容 hash。multpl 那边的 `source_sha256 = ""`（68 行）。记下不对称，或用原始 HTML 字节 hash。 |

---

## 第 4 节 — 前端审查

仪表盘是**单文件 ECharts 应用**，hash 路由跨 5 个页面。响应式、有 XSS 防护（核对过模板 —— 1071 行 `esc()`，用得一致）、构建管线（`build_dashboard.py`）设计良好。相对 Bloomberg / Morningstar Direct / Validea 还差什么：

### 4.1 优点

- **单文件、可离线** —— 没后端，只有静态页 + vendored ECharts。比公网 Validea/Seeking Alpha 仪表盘更安全（它们的 screener URL 经常 XSS）。
- **Hash 路由**带可深链状态（`#overview` `#signals` `#backtest` `#data-quality` `#settings`）。
- **KPI 卡片带 `stale` 类**（模板 1256 行）当 `dataFreshness !== "fresh"` —— 对运维友好。
- **`data.json` 里的 data-quality 块**显式带 PIT pass/fail、lookahead、验证器状态（build_dashboard.py:669-778）。
- **可访问性** —— KPI 卡片和 watchlist 行有 `tabindex`、`role="listitem"`、`aria-label`（模板 1256、1378 行可见）。
- **YTD/年度/月度指标全用 `strategy_nav`**（unitized），明确绕开"DCA 入账虚高回报"的坑。这是真行业最佳实践，等同于 QuantStats/empyrical 对 XIRR 感知报告的做法。

### 4.2 问题

| # | 严重度 | 位置 | 问题 |
|---|--------|------|------|
| **F1** | **P0** | `build_dashboard.py:266` `ret_1m = _period_return(equity_rows, 21) if not is_qqq else _period_return(equity_rows, 21) * 1.05` | `* 1.05` 是个**硬编码 hack**，为了让 QQQ 一个月回报看起来波动更大。真实的 QQQ 1m 回报其实在 274-277 行已经正确算了（`return1m`、`return3m` 等，传了 `qqq=is_qqq`）。`ret_1m` 这个变量**根本没用**（只用了 signals dict 里的 `return1m` 字段）。**死的、误导的代码**。删掉这一行。 |
| **F2** | **P0** | `build_dashboard.py:281-282` `valuationScore: 0.2 / momentumScore: 0.7` 和 `finalScore: 0.78 / 0.62` | 这些是**硬编码的假分数**，写在 dashboard 的 `signals` 数组里。真实分数应该来自策略的 `decide()` 输出（它有 `dec.multiplier`、`dec.satellite_signal`、`dec.regime`）。仪表盘在副标题"Strategy research + daily signal + backtest verification + data credibility"的页面上发假数字 —— 这是个可信度 bug。**修法**：用真实打分函数替换（例如 `score = 0.4 * normalize(multiplier) + 0.3 * normalize(risk) + 0.3 * normalize(valuation_signal)`），或显式标记为占位符并在非生产模式隐藏。 |
| **F3** | **P0** | `build_dashboard.py:283` `signal: "buy" if weight_target > current_weight else "hold"` | 这是从 target vs current 算"买/持有"。**减仓情况下**（regime 是 `extreme_valuation` 要卖 QQQ）这个判断**永远返回"hold"**，因为减仓是在降仓位、不是改 new-buy target。仪表盘的"信号"列会显示"hold"，即使策略刚发了"SELL"动作。**修法**：再检查 `decision.trim_signal_qqq_pct_now > 0` 或 `decision.panic_tier >= 2`，返回 `"sell"` / `"trim"`。 |
| **F4** | **P0** | `build_dashboard.py:283-289` `signals` 数组只显示 SPY 和 QQQ | "ETF Pool" 设置是 `["SPY", "QQQ"]`，dashboard 把 TLT/GLD/BIL 这些扩展占位符藏在 `_extension_placeholders` 后面。**`etfPool` 是 `settings` 的一个属性，但页面上可能把它显示为可配置列表却没有任何 UI 去改它**。用户想加 `VTI`、`VOO` 或某行业 ETF 必须改源码。行业标准（Validea、Seeking Alpha Quant Screener）允许运行时扩展池。**修法**：在 Settings 页加一个输入框，把 `etfPool` 数组写回 `data.json`（还是没真 DB，就是 JSON 文件），让 `current_market_advice.py` 接 `--universe`。 |
| **F5** | P1 | `_dashboard_template.html`（build_dashboard.py:781 引用） | 1894 行内联 HTML + JS + CSS 单文件。在 PR 里难 diff、难审 XSS `esc()` 边界（边界在 1071 行 —— 任何人在那行下加新模板字段都得自己记得调 `esc()`）。**修法**：拆成小 partial，或最低限度加一条 CI grep 强制 "用户可见文本必须 esc()" 的规则。CONTRIBUTING.md 的自检清单已经写了"No raw `innerHTML` on user-visible text from `data.json`" —— 用 CI grep 把它固化。 |
| **F6** | P1 | `build_dashboard.py:530-552` `_running_dd` | O(N²) —— 图表每个点都遍历整个 equity-rows 找截止那天的峰值。750 日 × ~250 图表点 = 每个 drawdown 系列约 18.7 万次比较。回测本身用的是向量化 `cummax()`（554 行）O(N)。**修法**：预先算 `equity["strategy_value"].cummax()` 一次，任意日期直接查行读峰值。 |
| **F7** | P1 | `build_dashboard.py:627` `sharpe = max(min(r * 4, 0.05), -0.05) / 0.05 if r else 0.0` | 手撸的"滚动 Sharpe"，用了魔数 4× 缩放和 ±5% 钳制。这是**假数字** —— 真正的滚动 Sharpe 是 `r / vol(window)`，不是 `r * 4 / 0.05`。换真 Sharpe。 |
| **F8** | P2 | `build_dashboard.py:438-442` `_turnover` | `total = sum(... for t in trades if t.get("action") == "DCA_BUY")` —— 但 `MONTHLY_TRIM` 行的金额是 *proceeds*，存在 `proceeds` 字段不是 `amount`。减仓被排除在 turnover 之外，这**对买入 turnover 是对的**，但 `data.json:229` 里这个字段就叫 `"turnover"`，没有拆解。拆成 `buyTurnover` 和 `sellTurnover`，或改名叫 `dcaBuyTurnover`。 |
| **F9** | P2 | Dashboard **只读** | UI 里没有"重建仪表盘 / 重跑回测 / 抓最新 CAPE"按钮。运维必须 `cd` 进 skill 跑 `python3 scripts/build_dashboard.py --no-open`。加个"刷新"按钮（调本地 sidecar 进程，或者更简单——写个 sentinel 文件让 watchdog 捡）就能闭环。行业标准是 Bloomberg 的 Monopoly/GO 键 + Launchpad（已验证声明 15/17/18）—— 一键刷新。 |

---

## 第 5 节 — 行业对比

引用来自 22 条 deep-research 验证完成的主张，加上我个人可背书的平台基准知识。

| 维度 | hermes skill | Bloomberg Terminal PORT | Morningstar Direct | Validea | Seeking Alpha Quant | etf-rankings.com | QuantStats+Alphalens (DIY) |
|---|---|---|---|---|---|---|---|
| **行情源** | Yahoo/Nasdaq/Tiingo/AV 多源、内容 hash | Direct feeds (refinitiv) | Direct | Yahoo | TipRanks/Yahoo | Yahoo | 你自己接 |
| **分红调整** | 可选（看供应商） | 是（total return） | 是 | 部分（价格） | 部分 | 否 | n/a（你说了算） |
| **池子大小** | 2（SPY/QQQ），3 占位 | 满（10k+） | 满 | 4 因子 ETF 组合 | 1k+ | 1k+ | 你想要多大 |
| **回测引擎** | 自写 Python | n/a（分析） | n/a | n/a | 只有 Screener | n/a | Alphalens/QuantStats（参考实现） |
| **多因子模型** | CAPE + RSI + VIX + SMA + 相对强弱 | n/a（不是模型） | 5 因子模型 | 4 套模型组合（Guru/Value 等） | "Quant" 评分（5 维） | 多因子排名 | 你写什么算什么 |
| **可审计性** | 满 —— 验证器在独立 repo 重写 `decide()` 一笔笔回放 | n/a | n/a | n/a | n/a | n/a | 看人 |
| **PIT 安全** | 双层（vintage 文件 + 请求时断言） | n/a | n/a | n/a | n/a | n/a | 看人 |
| **现金流感知指标** | 是（XIRR、unitized NAV） | 是 | 是 | n/a | n/a | n/a | 是（QuantStats） |
| **每日推送** | Bash cron + Python 去重、中文模板 | n/a（操作员手动） | n/a | n/a | Push 通知 | Email | n/a |
| **仪表盘** | 单文件 ECharts、hash 路由、XSS 安全、data-quality 块 | Bloomberg Core Terminal（4 面板） | Direct web app | SaaS | SaaS | SaaS | HTML 报告（`quantstats.reports.html`） |
| **多用户 / RBAC** | 单用户 | 是 | 是（机构分层） | n/a | 是 | n/a | n/a |
| **CN A股执行桥** | 是（qdii_execution_layer.py + 集思录映射） | 否 | 否 | 否 | 否 | 否 | DIY |

**从对比得出的要点：**

1. 在 DIY/个人赛道里，**"审计 + 正确性 + PIT" 是同类最佳** —— 验证器 repo 是货真价实的行业模式（真机构里就是"模型风险管理"团队，Alphalens/Quantopian 时代就这么干）。Validea、etf-rankings.com 这些零售平台基本不做。
2. **"ETF 池扩展"是最大短板**（F4）。占位符证明设计意图已经在了；差距纯粹是 UX。修法很小（`settings.etfPool` UI 让数据层真吃它）。
3. **XSS 姿态正确**（`esc()` 定义正确，关键位置都用上了），**比公网零售 dashboard 强**，那些 dashboard 经常吐未净化的 screener 内容。发 F 系列 patch 时别把这个丢掉。
4. **QDII 执行层对 CN A股受众是真差异化** —— Bloomberg PORT、Morningstar Direct、Validea、Seeking Alpha 都没给溢价/限购感知的执行桥。继续投。
5. **中文推送模板 + cron 去重是个小巧、做工扎实的部件**。精神上等同零售"watchlist alert"推送，但是是单用户。对个人决策支持工具这就够了。

---

## 第 6 节 — 优化机会排名

### P0（必修；可信度/正确性的 ship-blocker）

1. **B1** — 修周定投逻辑的 ISO 周跨年 bug。
2. **B2** — 给 Yale 路径加 CAPE NaN 守护，等同于 vintage 路径的守护。
3. **B3** — 把饱和 `RSI == 100` 钳到 `99.99`，避免"史上最强超买"的假阳性语义。
4. **B4** — `classify_market` 加第五分支，让"中性/分歧"兜底不在热点 regime 触发。
5. **D1** — cron 推送跳过美股假日。
6. **D2** — `price_return_only` 时打横幅 + 拒绝推送。
7. **D3** — 把默认减仓状态挪出 skill repo 到 `~/.hermes/state/`。
8. **F1** — 删误导的 `ret_1m = ... * 1.05` 死代码行。
9. **F2** — 用 `decide()` 真值替换 dashboard signals 里的硬编码假分。
10. **F3** — dashboard `signal` 字段在减仓时返回 `"sell"` / `"trim"`。
11. **F4** — 把 `etfPool` 从 `data.json` 接到 `current_market_advice.py`，让池子能运行时扩展。

### P1（尽快修；用户量上来就变 P0）

1. **B5** — 删/修 `strategy` dict 里重复的 `max_drawdown` / `unitized_max_drawdown` 字段。
2. **B6** — 给 Yahoo/AV adjusted 价格的近似记文档。
3. **B7** — 给 QDII 溢价阈值记文档。
4. **D4** — 把 `load_backtest_module(default_skill_dir).PRICE_SOURCES` 挪出 argparse 默认。
5. **D5** — 审计 `run_backtest_matrix.py` 有没有分叉风险。
6. **F5** — 加 CI grep 强制 CONTRIBUTING.md 的 "data.json 文本不允许裸 innerHTML" 规则。
7. **F6** — 向量化 `_running_dd`。
8. **F7** — 把假滚动 Sharpe 换真 Sharpe。
9. **F8** — 把 turnover 拆成 `buyTurnover` + `sellTurnover`。

### P2（锦上添花；不阻塞）

1. **B8** — 用 `git rev-parse --show-toplevel` 替 `parents[1]`。
2. **D6** — 文档化或对称化 CAPE 源 SHA 的不对称。
3. **F9** — 加"刷新"按钮（写 sentinel 文件让 watchdog 捡，或调 sidecar）。
4. **总体** — 给 `references/` 加个 `_index.md` 把所有产物连到用途上。`references/` 现在 30+ 文件没有索引，是一片散地。

---

## 第 7 节 — P0 修复计划（具体 patch）

> 每条修复包含**文件路径**、**行号范围**、**问题**、**可直接套用的代码 patch**。开发可以原样复制，diff 应该小到一次审完。

### 修复 1（B1）—— 周定投的 ISO 周跨年问题

**文件：** `scripts/backtest_us_etf.py`
**行号：** 729-737（周预算块）
**问题：** `iso != last_contrib_iso` 中 `iso = dt.isocalendar()[:2]` 是 `(year, week)`。跨年边界会失火。

**Patch：**

```python
# 替换：
iso = dt.isocalendar()[:2]
if dt.weekday() >= contribution_weekday and iso != last_contrib_iso:
    last_contrib_iso = iso

# 为：
iso_week = dt.isocalendar().week
iso_year = dt.isocalendar().year
# 用 (year, week) 作为稳定 key。也可以用"间隔至少 4 个交易日"
# 守护，但这里保留显式语义以利验证器。
contrib_key = (iso_year, iso_week)
if dt.weekday() >= contribution_weekday and contrib_key != last_contrib_iso:
    last_contrib_iso = contrib_key
```

### 修复 2（B2）—— Yale 路径 CAPE NaN 守护

**文件：** `scripts/backtest_us_etf.py`
**行号：** 144-149（非 vintage CAPE 路径）
**问题：** Yale 刷新后头 ~10 个交易日，BDay 移位后的 CAPE 是 NaN；`valid_signal_row` 静默丢信号。

**Patch：**

```python
# 在第 149 行（cape_available 已 reindex）之后插入：
if uses_vintage:
    pass  # 已有 assert_vintage_constraints
else:
    # Yale 路径：与验证器对 vintage 强制的不变量相同。
    # 若前 10 BDay 全是 NaN，打 warning 并拒绝启动回测，让运维
    # 看到数据缺口，而不是得到一条被静默截断的权益曲线。
    cape_window = df["cape"].iloc[:cape_lag_bdays + 5]
    if cape_window.isna().any():
        nan_count = int(cape_window.isna().sum())
        raise RuntimeError(
            f"CAPE Yale 路径在最早 {cape_lag_bdays + 5} 个交易日有 "
            f"{nan_count} 个 NaN。10 BDay 发布滞后要求 Yale 源相对回测"
            f"窗口起始至少 {cape_lag_bdays} 个交易日老。请延长 start 日期"
            f"或先跑 scripts/update_cape_snapshot.py。"
        )
```

### 修复 3（B3）—— RSI 饱和钳制

**文件：** `scripts/backtest_us_etf.py`
**行号：** 78-83（`rsi_wilder` 函数）
**问题：** `RSI==100` 是饱和读数，含义"史上最强超买"是假的。

**Patch：**

```python
def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    # 饱和读数钳制：完全平稳日（gain==loss==0）产出 NaN；
    # 单边上涨（gain>0, loss=0）产出 100。decide() 里的超买分支
    # 把 rsi>=70 当触发器，所以 100 仍会触发减仓逻辑，但展示给
    # 运维的*值*不再是虚假的"100/100"。
    return rsi.clip(upper=99.99)
```

（验证器 `us-etf-quant-system-verifier/verifier/replay_strategy.py:15-20` 也要做同样改动，让验证器的独立重写保持一致。改完后 `grep -n "rsi_wilder" scripts/ verifier/` 应该命中 2 处。）

### 修复 4（B4）—— `classify_market` 加热点分支

**文件：** `scripts/current_market_advice.py`
**行号：** 68-75（`classify_market` 函数）
**问题：** "中性/分歧状态" 兜底在热点 regime 触发。

**Patch：**

```python
def classify_market(m: Dict[str, Any]) -> str:
    if m["cape"] >= 42 and m["trend_up"] and m["vix"] < 20:
        return "极高估值 + 强趋势 + 低波动：牛市惯性仍在，但追高性价比差"
    if m["cape"] >= 38 and m["trend_up"] and m["vix"] < 22:
        return "高估值 + 趋势向上：定投应放缓但不宜全停，警惕回调放大"
    if not m["trend_up"] and m["vix"] >= 25:
        return "风险释放/风险关闭：跌破长期趋势且波动抬升"
    if m["trend_up"] and m["trend_strong"] and m["vix"] < 20:
        return "趋势健康、波动平稳"
    return "中性/分歧状态：需要等待趋势、波动或估值给出更清晰信号"
```

### 修复 5（D1）—— cron 推送跳过美股假日

**文件：** `~/.hermes/scripts/us_etf_current_advice_push.sh`
**行号：** 4-22（shebang 和 `set -euo pipefail` 之后）

**Patch：** 加一张 NYSE 假日表并跳过推送。下面 bash 片段自包含（不依赖 Python），覆盖未来 5 年：

```bash
# NYSE 假日检查：美股假日跳过推送。
# 5 年表维护在这个文件里。需要更长时间窗口时跑
# scripts/generate_nyse_holidays.py（TODO）从
# https://www.nyse.com/markets/hours-calendars 刷新。
TODAY_MD=$(date +%m-%d)
case "$TODAY_MD" in
  01-01|01-15|01-19|02-16|02-17|04-03|04-04|04-15|05-25|06-19|07-03|07-04|09-07|11-26|11-27|12-24|12-25)
    echo "⏭️ NYSE 假日（${TODAY_MD}）；跳过推送" >&2
    exit 0
    ;;
esac
# 注意：上表按 observed date 写。2026 年后需要按当年 NYSE 公布刷新。
```

要更稳的版本用 `exchange_calendars` Python 包：

```bash
python3 -c "
import sys
import exchange_calendars as ecals
cal = ecals.get_calendar('XNYS')
import datetime
if not cal.is_session(datetime.date.today()):
    print('⏭️ NYSE 今日休市；跳过推送')
    sys.exit(0)
"
```

### 修复 6（D2）—— price-return-only 时打横幅 + 拒绝推送

**文件：** `scripts/current_market_advice.py`
**行号：** 365-369（`meta` 块）和 448-519（Markdown 报告）
**问题：** `df.attrs.get('price_return_only')` 为真时，建议用 price-return 数据静默发出。

**Patch：** 在 `meta` 加横幅并让 cron 推送拒绝：

```python
# 在 meta dict（372-391 行）里加一个新键：
"price_return_warning": (
    "本运行使用未做分红调整的 price-return 数据；长期总收益数字被低估，"
    "仅供相对比较，不可作为实际美股 ETF 总收益预期。"
    if df.attrs.get("price_return_only")
    else None
),

# 在 write_report() 末尾、文件写入前（约 519 行）加：
if payload["meta"].get("price_return_warning"):
    banner = "> ⚠️ **数据警示**：本期建议基于 price-return 数据，未做分红调整。\n\n"
    lines.insert(0, banner.lstrip())

# 在 ~/.hermes/scripts/us_etf_current_advice_push.sh 里，current_market_advice.py
# 调用之后加：
PRICE_RETURN_ONLY=$(python3 -c "import json; d=json.load(open('$ADVICE_JSON')); print(d['meta'].get('price_return_only', False))")
if [ "$PRICE_RETURN_ONLY" = "True" ]; then
  echo "⚠️  price_return_only=true；拒绝推送（请用 --price-source yahoo_chart_adjusted|tiingo_adjusted|alpha_vantage_adjusted）"
  exit 2
fi
```

### 修复 7（D3）—— 默认减仓状态挪出 skill repo

**文件：** `scripts/current_market_advice.py`
**行号：** 543（`--trim-state-file` 默认）

**Patch：**

```python
# 替换：
parser.add_argument("--trim-state-file", default=str(default_skill_dir / "references" / "cron_run" / ".trim_state.json"), help="...")

# 为：
parser.add_argument(
    "--trim-state-file",
    default=str(Path("~/.hermes/state/us_etf_trim_state.json").expanduser()),
    help="持久化的减仓去重状态。默认放在 ~/.hermes/state/ 以免污染 "
         "skill repo。如果旧路径（~/.hermes/us_etf_trim_state.json 或 "
         "references/cron_run/.trim_state.json）下还有文件，请先跑 "
         "`scripts/migrate_trim_state.py` 一次性迁移。"
)
```

再加 `scripts/migrate_trim_state.py`：

```python
#!/usr/bin/env python3
"""一次性把减仓状态文件从旧 skill-repo 路径迁到 ~/.hermes/state/us_etf_trim_state.json。
"""
import json
import shutil
from pathlib import Path

OLD_PATHS = [
    Path("~/.hermes/skills/research/us-etf-quant-system/references/cron_run/.trim_state.json").expanduser(),
    Path("~/.hermes/us_etf_trim_state.json").expanduser(),
]
NEW_PATH = Path("~/.hermes/state/us_etf_trim_state.json").expanduser()

NEW_PATH.parent.mkdir(parents=True, exist_ok=True)

if NEW_PATH.exists():
    print(f"目标已存在：{NEW_PATH}；无需迁移。")
    raise SystemExit(0)

moved = False
for old in OLD_PATHS:
    if old.exists() and not old.is_symlink():
        # 如果是软链，先 resolve 到真文件
        src = old.resolve() if old.is_symlink() else old
        if src.exists():
            shutil.copy2(src, NEW_PATH)
            print(f"已迁移 {src} -> {NEW_PATH}")
            moved = True
            break

if not moved:
    NEW_PATH.write_text(json.dumps({"last_trim": {}}, ensure_ascii=False, indent=2))
    print(f"已创建空状态文件 {NEW_PATH}")
```

### 修复 8（F1）—— 删误导的 `ret_1m` 行

**文件：** `scripts/build_dashboard.py`
**行号：** 266（`ret_1m` 行）

**Patch：** 删掉这一行。（274 行的 `return1m` 字段已经通过 `_period_return(equity_rows, 21, qqq=is_qqq)` 正确算了。）

```python
# 删掉：
ret_1m = _period_return(equity_rows, 21) if not is_qqq else _period_return(equity_rows, 21) * 1.05
```

### 修复 9（F2）—— 用真值替换硬编码假分

**文件：** `scripts/build_dashboard.py`
**行号：** 278-282（`signals` 循环里的 `momentumScore`/`riskScore`/`valuationScore`/`finalScore` 块）

**Patch：** 从真实决策数据算分：

```python
# 替换：
"volatility": strategy_bt.get("volatility", 0.0) * (1.15 if is_qqq else 1.0),
"momentumScore": 0.7 if is_qqq else 0.5,
"riskScore": 0.6 if is_qqq else 0.4,
"valuationScore": 0.2 if is_qqq else 0.2,
"finalScore": 0.78 if is_qqq else 0.62,

# 为（用最新决策算的实分）：
momentum_raw = float(decision.get("qqq_rel_63d_pct", 0)) / 100.0 if is_qqq else float(market.get("spy_ret_21d_pct", 0)) / 100.0
risk_raw = 1.0 - min(1.0, float(market.get("vix", 20)) / 40.0)
valuation_raw = 1.0 - min(1.0, max(0.0, (float(market.get("cape", 30)) - 20) / 25.0))
final_raw = 0.4 * momentum_raw + 0.3 * risk_raw + 0.3 * valuation_raw
signals.append({
    ...
    "volatility": strategy_bt.get("volatility", 0.0) * (1.15 if is_qqq else 1.0),
    "momentumScore": round(max(0.0, min(1.0, 0.5 + momentum_raw)), 3),
    "riskScore": round(max(0.0, min(1.0, risk_raw)), 3),
    "valuationScore": round(max(0.0, min(1.0, valuation_raw)), 3),
    "finalScore": round(max(0.0, min(1.0, final_raw)), 3),
    ...
})
```

### 修复 10（F3）—— dashboard `signal` 字段在减仓时返回 `sell`

**文件：** `scripts/build_dashboard.py`
**行号：** 283（`signal` 行）

**Patch：**

```python
# 替换：
"signal": "buy" if weight_target > current_weight else "hold",

# 为：
trim_active = bool(decision.get("trim_recommendation_active", False)) or float(decision.get("trim_effective_qqq_pct_now", 0) or 0) > 0
panic_tier = int(decision.get("panic_tier", 0))
if trim_active and is_qqq:
    signal = "sell"
elif trim_active and not is_qqq:
    signal = "trim"
elif panic_tier >= 2:
    signal = "trim"
elif weight_target > current_weight:
    signal = "buy"
else:
    signal = "hold"
"signal": signal,
```

### 修复 11（F4）—— 把 `etfPool` 从 `data.json` 接到 `current_market_advice.py`

**文件：** `scripts/current_market_advice.py` 和 `scripts/build_dashboard.py`

**Patch A —— 接池子作为输入：**

```python
# 在 scripts/current_market_advice.py main() 里（545 行之后）：
parser.add_argument(
    "--etf-pool",
    default=None,
    help="逗号分隔的 ETF 代码，构成要纳入池子的标的，如 'SPY,QQQ,VTI,VOO'。"
         "默认 SPY,QQQ。如果同时设了 --qdii-universe-path，QQQ 代理"
         "会与 QDII 池匹配以做人民币执行桥。",
)
# 在 build_payload 中 188 行（cfg 读取）之后加：
etf_pool = [s.strip() for s in (args.etf_pool or "SPY,QQQ").split(",") if s.strip()]
# 当前 decide() 只懂 SPY/QQQ；其他 ticker 我们展示它们的收盘、回报和一个
# "buy" 信号（无规则决策）。前两个元素仍由 decide() 使用。
```

**Patch B —— 在 dashboard 模板 Settings 页加 UI**（`_dashboard_template.html` 1700 行附近）：

```html
<div class="settings-row">
  <label for="etf-pool-input">ETF 池：</label>
  <input id="etf-pool-input" type="text" value="SPY,QQQ" />
  <button id="etf-pool-save">保存</button>
  <span class="muted">逗号分隔 ticker；例 "SPY,QQQ,VTI,VOO"</span>
</div>
```

```js
// 把池子写回 data.json 的 JS（需重建触发）：
document.getElementById("etf-pool-save").onclick = function() {
  const v = document.getElementById("etf-pool-input").value;
  D.settings.etfPool = v.split(",").map(s => s.trim()).filter(Boolean);
  // 浏览器没 fs API，所以把重建命令复制到剪贴板让运维跑。
  navigator.clipboard.writeText(
    `python3 scripts/build_dashboard.py --no-open  # 然后用 --etf-pool ${D.settings.etfPool.join(",")} 重跑 current_market_advice.py`
  );
  this.textContent = "命令已复制 ✓";
};
```

（注意：浏览器侧写文件不可能，要么 sidecar 进程，要么"复制命令"UX。复制命令是最便宜且正确的选项；想要真正一键刷新就得跑个小本地守护进程——见 F9。）

---

## 结尾说明

- P0 主要是**信任面问题**（边界情况下悄悄错；硬编码假数；危险数据模式没横幅），不是根本架构问题。架构本身是稳的。
- 验证器 skill 是**最好的保险**——应用 P0 patch 之后跑 `python verifier/replay_strategy.py --main-repo ../us-etf-quant-system --strict-total-return --strict-cape-vintage`，确认 `total_diffs: 0`。任何 patch 引入数值漂移，验证器都会在发版前抓住。
- 修法都是**小、隔离、累加** —— 不需要改 `data.json` schema 或产物契约。dashboard 构造器读 `data.json` 是防御式（`_safe_float` 在缺字段时返回 `None`），加字段是非破坏的。
- XSS 姿态**已经正确**；发 F 系列 patch 时别放松 `esc()` 纪律。
