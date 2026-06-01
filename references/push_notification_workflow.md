# 推送与当前市场建议工作流

适用范围：`us-etf-quant-system` 的日常建议生成、中文通知模板、去重推送。

## 1. 输出目标
- 面向中文读者，先给**可执行建议**，再给支撑指标。
- 不要只给 regime / multiplier / CAPE/RSI 原始值；必须把指标翻译成“怎么看”。
- 输出里要显式展示：
  - SPY 当日涨幅
  - QQQ 当日涨幅
  - 定投倍率
  - SPY/QQQ 建议买入占比
  - 按用户现有持仓换算后的建议买入金额

## 2. 推荐通知结构

采用**简洁 bullet 模板**，不要用 Markdown 表格。Telegram 会自动把表格改写成较松散的卡片，用户更喜欢如下紧凑格式：

1. 标题：`📊 美股 ETF 定时推送`
2. 第一行：市场日期 + SPY/QQQ 当日涨幅
3. 一句话结论：市场状态中文摘要 + 操作标签
4. 核心数据：
   - SPY：收盘价、距 SMA200、SMA50/SMA200，并加一句中文说明
   - QQQ：收盘价、QQQ/SPY 63日相对强弱，并加一句中文说明
   - RSI14：数值 + 超买/超卖说明
   - 近21日 SPY + 252日回撤，并说明短期动量/回撤含义
   - VIX：数值 + 20日均值 + 恐慌程度说明
   - Shiller CAPE：数值 + Regime + 估值区间说明
   - Trend up / Trend strong / Risk off，并说明这些布尔值怎么理解
5. 系统建议：
   - 定投倍率
   - SPY/标普 与 QQQ/纳指 买入分配
   - QQQ 减仓信号
   - 中文化规则依据
6. 持仓换算：
   - 总市值
   - 原周定投 → 本期建议买入
   - 分资产买入金额
   - 买入后预估权重
7. 定时推送说明：工作日 9 点，建议无变化则跳过。
8. 国内执行层（如可用）：QDII/LOF 候选基金、溢价率、申购状态、成交额/流动性，以及“正常买 / 减半买 / 暂缓买”的执行动作。若执行层数据过期，必须显式提示“数据过期，不作为下单依据”。
9. 自动定投差额：把模型建议金额与 `portfolio_config.json` 中实际 active 自动定投安排对比，展示总额差额以及标普/纳指分资产多买或少买金额。

## 3. 去重逻辑
若以下 6 个字段与前一交易日完全一致，则跳过推送：
- `action_label`
- `dca_multiplier`
- `spy_weight_pct`
- `qqq_weight_pct`
- `trim_signal`
- `regime`

说明：这比全文比较更稳，既能避免格式噪声，也能保证真正策略变化时触发推送。

如果推送增加国内 QDII 执行层，可以额外把“执行动作档位”纳入去重，而不是把每日涨跌纳入去重：
- 标普候选基金 action：正常 / 减半 / 暂缓
- 纳指候选基金 action：正常 / 减半 / 暂缓
- 溢价档位：正常 / 偏高 / 过高
- 申购状态：open / suspended
- 执行层数据是否过期

推送正文应展示“本次触发原因 / 相比上次变化”，例如定投倍率、买入分配、Regime、减仓信号或 QDII 执行动作发生变化。若是强制推送或周报且建议未变，明确写“建议未变，本次仅做状态复盘”。

## 4. 编码/脚本实现经验
不要用 bash 主导解析含中文字段的 JSON 再拼接 Markdown。该场景容易因 shell 编码/变量处理导致乱码或 `unbound variable` 类问题。

推荐做法：
- 用 Python 统一完成：
  - 调用建议脚本
  - 读取 JSON
  - 解析字段
  - 渲染中文 Markdown
  - 维护去重状态文件
- shell 最多只做薄封装，不承担中文 JSON 模板拼接。

## 5. 输出风格要求（从本次用户反馈沉淀）
- 不能抽象；必须“看得懂、能执行”。
- 指标后面加一句中文释义，比只给数字更重要。
- 对金融/量化输出，优先给具体数值、比例、金额，不要停留在策略名词层。

## 6. 已验证实现模式：只加非 QDII 的执行提醒

当用户明确要求“除了 QDII 执行层之外”的推送增强时，优先在 `~/.hermes/scripts/us_etf_current_advice_push.py` 增加以下两个轻量区块，不要擅自接入 `qdii_execution_layer.py`：

### 本次触发原因

- 在写入新的 `.advice_state.json` 之前保留旧 state。
- 对比去重签名字段：`action_label`、`dca_multiplier`、`spy_weight_pct`、`qqq_weight_pct`、`trim_signal_pct`、`regime`。
- 正文展示变化，如：`定投倍率：1x → 0.75x`、`SPY买入占比：50% → 40%`。
- 如果是 `US_ETF_FORCE_PUSH=1` 且建议字段未变，展示：`建议未变，本次仅做状态复盘。`

### 自动定投差额

- 从 `~/.hermes/portfolio_config.json` 的 `plan.items[]` 读取 `status != "paused"` 的 active 自动定投项目。
- 用 `weekly_equivalent` 汇总当前自动单总额，并按 `target` 关键词归类到标普/SPY、纳指/QQQ、other。
- 与 `current_market_advice.json` 里的 `recommended.total_buy`、`recommended.spy_buy`、`recommended.qqq_buy` 对比。
- 正文展示：
  - 模型建议：本期买多少，标普/纳指各多少。
  - 当前自动单：本周约多少，标普/纳指各多少。
  - 若不调整：总额、标普、纳指分别多买/少买多少。
  - 明确说明这是手动追加/暂停/临时调低参考，不会自动改银行或券商定投。

### 验证方式

- `python -m py_compile ~/.hermes/scripts/us_etf_current_advice_push.py`
- 备份并恢复 `references/cron_run/.advice_state.json`，用 `US_ETF_FORCE_PUSH=1` 模拟输出，避免测试污染真实去重状态。
- 再构造一个临时旧 state，确认“本次触发原因”能列出变化字段。
