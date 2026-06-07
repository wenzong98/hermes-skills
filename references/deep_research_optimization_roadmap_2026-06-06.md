# us-etf-quant-system 优化路线图（2026-06-06 调研）

> **调研方法**：deep-research 工作流，5 个并行搜索角度，23 个原始来源，87 条可证伪断言，25 条进入对抗验证（每条 3 票），15 条通过、10 条被反驳。
>
> **调研基线**：本地 `us-etf-quant-system`（DCA + CAPE + 趋势 + VIX 规则系统，~5,000 LOC Python，Python + pandas/numpy + SQLite，无 ML/期权/实盘）和独立 `us-etf-quant-system-verifier`（re-implement 规则）。

---

## 0. 摘要（先看这里）

1. **业内没有"开箱即用"的同类**——DCA + Shiller CAPE + 风险预算的开源项目不存在；我们占据了一个真正稀缺的细分赛道。
2. **最容易引进的两块积木**：(a) **Riskfolio-Lib** 作为风险预算/仓位大小层；(b) **CPCV + Deflated Sharpe Ratio** 作为回测过拟合防御层。
3. **不要碰**已经停摆的 Backtrader（最后 commit 2023-04-19，~37 个月前）、mlfinlab（公开仓库自 2021-12-01 起只读 + 商业授权）。
4. **P0 必做 4 项**：(1) Deflated Sharpe / 多重检验报告 (2) Regime-aware 评估 (3) 波动率目标 sizing (4) Live paper-trading 跑道。
5. **P1 显著提升 5 项**（见后文，按 ROI 排序）。
6. **P2 探索性 3 项**（HMM/贝叶斯 regime detection、期权 overlay、broker 接入）属于 R&D，需要单独立项。

---

## 1. 业内方案对比（角度 1）

> 调研断言均通过 3 票对抗验证（来源 = 官方仓库 / 官方文档）。

| 项目 | 是否活跃 | DCA 支持 | CAPE/估值 | 风险预算/Vol-targeting | Walk-forward | 我们该不该用 |
|---|---|---|---|---|---|---|
| **Riskfolio-Lib** | ✅ v7.3.0，2026-05-31 | ❌ 需自己写 | ❌ | ✅ **最完整**（Risk Parity、HRP、HERC、NCO、Black-Litterman、MVSK、Entropy Pooling、22–37 个凸风险度量） | 部分 | **引进，作为 sizing 层** |
| **QuantConnect Lean** | ✅ 持续维护 | ❌ | ❌ | ✅ 5 阶段框架（Universe→Alpha→Portfolio→Risk→Execution），4 个开箱风险模型（per-security drawdown cap、sector cap、trailing-stop、take-profit）+ MeanVarianceOptimization | ✅ | 借鉴 5 阶段拆分；不整体替换（我们已有自有引擎） |
| **Zipline-reloaded** | ✅ 由 Stefan Jansen 维护（ML 配套书） | ❌ **README 零** | ❌ | ❌ | ❌ | **不引入**；定位是 ML 研究 / 单标的择时 |
| **vectorbt** | ✅ v1.0.0，2026-04-22，Rust 内核 | ❌ 不强调 | ❌ | ❌ 开源版无 risk-parity | ✅ 显式 | 借鉴信号+多资产扫描+walk-forward 的代码范式 |
| **Backtrader** | ❌ **停摆**，最后 commit 2023-04-19 | ❌ | ❌ | 部分 | 部分 | **不要碰** |
| **Qlib（Microsoft）** | ✅ | ❌ | ❌ | 部分 | ✅ | **范式不匹配**（AI 平台，23+ 模型 zoo：GBDT/LSTM/Transformer/GAT/RL），我们规则化路径 |
| **Hudson & Thames mlfinlab** | ❌ **公开仓库仅作 issue tracker**，4.8k⭐ 但仓库"all rights reserved"，真二进制走商业授权 | — | — | — | ✅ | **不引入**（无免费可依赖的代码分发渠道） |

**商业产品作为行为参考**：
- **Betterment** 公开做法：101 档 stock-bond 切片 + 估值/价值倾斜 + 独立 cash sleeve（拿 3.25% APY）；多策略并存于同一账户。我们已有 0x-3x multiplier + cash reservoir，对齐方向正确，但**缺少 cash sleeve 显式收益建模**（"现金水库"是名义上的，未计利息）。  
  来源：[betterment.com/portfolio](https://www.betterment.com/portfolio/)  
- **Qplix** 等机构 robo：factor-based risk budgeting + 多账户多币种 — 适合 A 股 QDII 多账户/多币种扩展。
  来源：[qplix.com/wealth-management](https://qplix.com/wealth-management/)

**结论**：无现成竞品可整体替换。**最大两个增量是 Riskfolio-Lib（sizing 层）和 Lean's 5-stage 架构思维（拆分/解耦）**。

---

## 2. GitHub 活跃开源项目（角度 2）

> 调研范围内确认活跃的：Riskfolio-Lib、QuantConnect Lean、vectorbt、Zipline-reloaded、Qlib。
>
> 没在调研中浮现的活跃 SPY/QQQ 专门项目（需要补充查证）：`mementum/backtrader` 周边生态、PyPortfolioOpt、bt（alimatefacadeh/bt）、empyrical、Riskfolio-Lib 上面的 `riskfolio` 教程生态。

**我们比他们多/少的：**

| 维度 | 我们 | 大多数开源项目 |
|---|---|---|
| CAPE 估值规则 | ✅ Shiller 五档 | 几乎全无（量化框架默认不内置估值） |
| 周频 DCA + cash reservoir | ✅ | 无（Qlib 之类是 top-k drop / weight target） |
| 无前视 PIT 验证 | ✅ + 独立 verifier | Lean 有，Zipline 默认有，但需要自己写严格证明 |
| QDII 跨市场执行层 | ✅ | 无 |
| Risk Parity / 波动率目标 | ❌ | Riskfolio-Lib / PyPortfolioOpt ✅ |
| Regime detection（HMM / 贝叶斯） | ❌ | Qlib 有，但我们要的不是 ML |
| 实时 broker 直连 | ❌ | Lean 强（IBKR/Alpaca/Tradier） |
| Walk-forward + CPCV | ❌ | vectorbt 有 walk-forward；CPCV 要自己写 |

**借鉴路径**：
- 拿 Riskfolio-Lib 的 22 个凸风险度量 + risk-contribution 约束；
- 拿 Lean's 4 个 prebuilt risk model（drawdown cap、sector cap、trailing stop、take profit）作为我们 stop-profit / risk-off trim 逻辑的**对标基线**（验证我们的版本没有缺失能力）；
- 拿 vectorbt 的信号化 + 多维参数扫描范式重写 `optimize_params.py`。

---

## 3. 未使用的信号与数据源（角度 3）

> 部分角度 3 来源在调研中被 1-2 / 0-3 票反驳（例如 Cboe term-structure 上"曲线形状作为 DCA 信号"这一延伸解读被 0-3 反驳），所以本节是**结合项目需求 + 通过的金融共识**整理的方向，**未做单点强断言**。

### 3.1 立即可加（数据可免费获取）

| 信号 | 获取 | 预期收益 | 难度 | 在我们系统里怎么用 |
|---|---|---|---|---|
| 10y-2y 利差（yield curve） | FRED `T10Y2Y` | 中（衰退预警） | 低 | 现有 trend filter 之外的"宏观 trend cap" |
| HY 利差 OAS | FRED `BAMLH0A0HYM2` | 中（信用风险） | 低 | 现有 VIX 之外的"信用 stress"过滤 |
| % of SPY above 200dema | 自算 | 中（广度） | 低 | 现有 SPY vs SMA200 之外的"参与度"信号 |
| AAII 情绪差分（bull-bear spread） | AAII 公开 CSV | 中（逆势） | 低 | panic tier 里的一个加分项 |
| DXY | FRED `DTWEXBGS` | 低 | 低 | 仅在极端位置（>105 / <95）时给 QQQ tilt |
| ETF 资金流（SPY/QQQ 周度） | etf.com / SSGA | 中 | 中 | 反向因子（流入过热 → 减仓）；难点：原始数据需付费 |

### 3.2 进阶（需要额外数据通道）

| 信号 | 获取 | 预期收益 | 难度 |
|---|---|---|---|
| **VIX 期货 term structure**（contango/backwardation） | CFTC COT + CBOE futures | 中-高 | 中 |
| **VIX 9D / VIX 30D / VIX 3M** 比值 | CBOE 历史 CSV | 中 | 中 |
| put/call ratio（equity-only） | CBOE 历史 | 中 | 中 |
| 恐惧贪婪指数（CNN） | scraping | 低（黑箱聚合） | 低 |
| McClellan Summation Index | 自算或付费 | 中 | 中 |

### 3.3 多标的扩展（结构变化）

当前仅 SPY+QQQ 二元：

| 扩到 | 调整内容 | 预期收益 | 难度 |
|---|---|---|---|
| 加 TLT（美债 20y+）做 risk-off | 80/20 core-satellite 加一档 satellite "defensive" | 高 | 中 |
| 加 GLD | 通胀对冲 | 中 | 中 |
| 加 IWM（小盘） | size 因子 | 低-中 | 中 |
| 加 VEA（发达市场） | 地理分散 | 中（高相关） | 中 |
| 加 BND（总债） | 替代 TLT | 中 | 中 |

### 3.4 估值替代 / 增强

| 替代 | 改进点 | 难度 |
|---|---|---|
| ECY（Earnings / Cyclically adjusted Yield = 1/CAPE） | 沿用 yield 框架，更直接 | 低 |
| 利润率调整 PE（Shiller + 利润率 z-score） | 分离"估值高"与"利润高" | 中 |
| 多 CAPE 资产并行 | CAPE-only on SPX；QQQ 缺权威 CAPE，用 P/E + growth proxy 替代 | 中 |

### 3.5 Regime detection

**HMM / 贝叶斯 regime**（调研中 0-3 票被打掉，结论是"作为 DSR/Tier 调节的副作用信号可用，作为主仓位驱动则证据不足"）。
- **P2 探索**：用 GaussianHMM 在 3-4 状态（low-vol bull / high-vol bull / low-vol bear / crisis）下做 regime tag，附加给 0x-3x multiplier 一个 -0.25 ~ +0.25 的微调；不替换主逻辑。
- 难度：高，预期收益：低-中（regime 切换有滞后）。

---

## 4. 回测与验证方法（角度 4）

> DSR 在原 SSRN PDF 引用时 0-3 被打掉（"DSR 公式精确表达式"那条），但 **DSR 概念（trial-count penalty on Sharpe）通过 2-1 验证，且是金融 ML 教材的常驻章节**。Lopez de Prado, *Advances in Financial Machine Learning* (Wiley 2018) 是公认参考。

### 4.1 必做 P0（防御性）

| 项目 | 难度 | 预期收益 | 备注 |
|---|---|---|---|
| **Deflated Sharpe Ratio (DSR) 报告** | 低 | **高**（数小时内加 50 行） | 把 N=trial count 算出来（N = 信号×阈值×倍数档位的笛卡尔积），套 DSR 公式。**我们的 23.91% XIRR 在 DSR 调整后大概率被高估**——这正是要警示的。 |
| **多重检验报告**（Bonferroni / BH-FDR） | 低 | 中 | 同上，把 N 种变体的 Sharpe 都列出来，做 FDR。 |
| **Monte Carlo bootstrap on equity curve** | 低 | 中 | 已有 daily NAV，做 1000 次重抽样给出 XIRR 的 90% CI。 |
| **Regime-aware evaluation** | 中 | **高** | 至少把回测窗口拆成：2020 COVID 段、2022 熊市段、2023-2024 反弹段、2025+ AI 段，分别报告 XIRR/DD。**当前一个合并 XIRR 隐藏了大量 regime-specific 行为**。 |

### 4.2 应做 P1

| 项目 | 难度 | 预期收益 | 来源 |
|---|---|---|---|
| **CPCV** (Combinatorially Purged Cross-Validation) | 中 | 中-高 | Lopez de Prado, *Advances in Financial ML*, Ch. 7 & 12 |
| **Walk-forward** 替代静态 3y | 中 | 高 | vectorbt README 显式支持 |
| **Slippage & commission** 显式建模 | 中 | 中 | Lean reality-modeling 参考 |
| **多起点 / 随机起点 sensitivity** | 低 | 中 | 自己写 200 行，参数随机打乱再回测 |
| **数据 PIT 强化**（earnings announcement 边界、split/dividend 重叠） | 中 | 中 | 我们的 `--require-adjusted` 是骨架，要补全 |

### 4.3 中长期

| 项目 | 难度 | 预期收益 |
|---|---|---|
| Almgren-Chriss 冲击成本模型 | 高 | 低（我们 DCA 量级小，bips 级可忽略） |
| Probability of Backtest Overfitting (PBO) | 高 | 中 |
| 白噪声方差下限（Lo 2002） | 中 | 中 |

---

## 5. 风险与组合管理（角度 5）

> Lean risk models（drawdown cap、sector cap、trailing stop、take profit）4 个都是 3-0 通过；IBKR TWS API + ib_insync 3-0 通过。

### 5.1 仓位大小（sizing）— **P1 必做**

**当前**：`multiplier × weekly_budget`，0x-3x 离散。
**业内主流**：
- **Volatility targeting**（Riskfolio-Lib 可直接接）—— 把目标组合年化波动率锁在 10%/15%，反推仓位；
- **Risk Parity** —— 各资产对组合风险贡献相等（不依赖预期收益估计）；
- **CPPI**（保本底线）—— 不适合纯多头 DCA 框架，可作为"地板保护"；
- **Kelly fraction**（对数均值意义下）—— Riskfolio-Lib 已内置 *Logarithmic Mean Risk*。

**推荐**：在现有 0x-3x 离散 multiplier 之上加一层 **vol-targeting 平滑层**（Riskfolio-Lib 算 21d / 63d realized vol，映射到 [0.5, 1.5] 缩放乘数），保留离散档位的可解释性，连续层做精细化。

### 5.2 风险模型 — P1

| 风险模型 | Lean 是否有 | 我们是否有 | 缺口 |
|---|---|---|---|
| Per-security drawdown cap | ✅ | 部分（QQQ trim） | 缺 SPY / cash drawdown cap |
| Sector-exposure cap | ✅ | 隐式（80/20 core-satellite） | 缺显式 cap |
| Trailing stop | ✅ | ❌ | **加**（组合层面 8% trailing） |
| Take profit | ✅ | ❌ | 当前是 monthly capped trim；可补一个组合层面 25% take-profit |

### 5.3 实盘/paper trading 跑道 — **P0**

**最便宜的路径**：Alpaca paper trading（免费，REST API）→ `ib_insync`（IBKR 真实账户）。

> 调研断言：QuantConnect Lean 与 IBKR / Alpaca / Tradier 都有官方 reality-modeling 支持（3-0 验证）。

| 步骤 | 难度 | 预期收益 |
|---|---|---|
| 把 `current_market_advice.py` 输出加一个 `execution_plan.json` | 低 | 高（人工执行脚本化） |
| 接 Alpaca paper trading | 中 | **高**（真正 end-to-end） |
| 接 IBKR TWS (ib_insync) | 高 | 中（需要账户/合规） |

**最大价值是 paper trading 至少跑 1 个完整 regime**（推荐 6-12 个月）才能知道回测 vs 实际差异。

### 5.4 多策略组合 — P2

当前是**单策略**。多策略（价值 + 动量 + 趋势）混合通常改善 Sharpe 1.5-2x。

但要小心：多策略 ≠ 同一个标的多个 signal ≠ 自动提升。属于 R&D。

### 5.5 期权 overlay — P2

调研中未深度涉及。Collar / put-spread 可以给现金水库加一层下保护；但**对 A 股 QDII 来说几乎不可行**（缺 QDII 场内期权）。仅在美股直接执行时相关。

---

## 6. 排序后的 Actionable 路线图

### 🔴 P0：不做有风险或不可信（应当最先做）

| # | 优化项 | 难度 | 预期收益 | 触发原因 | 来源 |
|---|---|---|---|---|---|
| P0-1 | **Deflated Sharpe + 多重检验报告** | 低 | 高 | 当前 23.91% XIRR 未做 trial-count 调整；存在"选最优参数"偏置 | Lopez de Prado, *Advances in Financial ML*, 2018 |
| P0-2 | **Regime-aware 评估**（按 bull/bear/crisis 拆解 3y 回测） | 中 | 高 | 当前 1 个合并 XIRR 隐藏 regime-specific 行为 | 直接对照 vectorbt 多 regime 范式 |
| P0-3 | **Vol-targeting 平滑层**（Riskfolio-Lib 接入 21d/63d realized vol → [0.5,1.5] 缩放） | 中 | 高 | 把离散 0x-3x 改成"档位 + 平滑"，提升风险调整后收益 | [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) |
| P0-4 | **Paper trading 跑道**（Alpaca 优先，IBKR 备选） | 中 | **极高**（验证全链路） | 3 年回测 vs 6 个月 paper 是质的差异 | Lean IBKR/Alpaca 文档 |

### 🟡 P1：明显提升（下一迭代）

| # | 优化项 | 难度 | 预期收益 | 来源 |
|---|---|---|---|---|
| P1-1 | Walk-forward 替代静态 3y backtest | 中 | 高 | [vectorbt](https://github.com/polakowo/vectorbt) |
| P1-2 | Monte Carlo bootstrap on equity curve（XIRR 90% CI） | 低 | 中 | Lopez de Prado 教材 |
| P1-3 | Slippage / commission 显式建模 | 中 | 中 | Lean reality-modeling |
| P1-4 | 把 cash sleeve APY 显式建模（Betterment 那种 100% cash 子账户） | 低-中 | 中 | [Betterment portfolio](https://www.betterment.com/portfolio/) |
| P1-5 | 多标的扩展（至少加 TLT 做 risk-off） | 中 | 中-高 | 我们当前二元结构缺真正的 defensive sleeve |

### 🟢 P2：探索性（中长期 R&D，单独立项）

| # | 优化项 | 难度 | 预期收益 | 备注 |
|---|---|---|---|---|
| P2-1 | HMM / 贝叶斯 regime detection | 高 | 低-中 | 作为 multiplier 微调，不作主驱动 |
| P2-2 | 多策略组合（value + momentum + trend） | 高 | 中-高 | 需先有第二/第三个独立可执行策略 |
| P2-3 | 期权 overlay（collar / put-spread） | 高 | 中 | 仅美股直接执行场景，QDII 不可行 |
| P2-4 | IBKR TWS 直连 | 高 | 中 | paper trading 后再做 |
| P2-5 | CPCV、PBO、白噪声方差下限 | 高 | 中 | 学术级别严谨性 |

### ⚫ 不要做（基于调研证据）

| # | 反向建议 | 依据 |
|---|---|---|
| N-1 | 不要迁移到 Backtrader | 最后 commit 2023-04-19，~37 个月停摆 |
| N-2 | 不要用 mlfinlab 作为依赖 | "all rights reserved" 公开仓库 + 4.5 年无代码更新 |
| N-3 | 不要整体换 Zipline-reloaded | 定位是 ML 单标的择时，零 DCA/估值/风险预算 |
| N-4 | 不要抄 Qlib | ML 范式与我们的规则化路径不匹配 |
| N-5 | 不要把 VIX 期货 term structure 当主信号 | 调研中 0-3 被打掉（DCA 时机 vs 曲线形状，业内无强证据） |

---

## 7. 立即可做（这周就能起手） vs 下一迭代 vs 中长期

### 立即可做（1-2 周）
1. **DSR + 多重检验报告**——纯计算，~50-100 行，~1 天
2. **Monte Carlo bootstrap on equity curve**——已有 daily NAV，~50 行，~半天
3. **Regime-aware 报告**（拆分 3y 到 4 个 regime）——SQL/pandas 切片，~1 天
4. **Cash sleeve APY 显式建模**——参数化，~半天
5. **多起点 sensitivity**（参数随机打乱）——基于 `optimize_params.py`，~1 天

### 下一迭代（1-3 月）
1. 接入 Riskfolio-Lib 的 vol-targeting + risk-parity 平滑层
2. Slippage / commission 显式
3. Walk-forward 替换静态 backtest
4. 加 TLT 做 risk-off sleeve（多标的扩展）
5. Paper trading 跑道（Alpaca）

### 中长期 R&D（3-12 月）
1. HMM regime detection（轻量版）
2. 多策略组合（并行第二条策略）
3. IBKR TWS 直连
4. 期权 overlay（仅美股直接执行）
5. CPCV / PBO / 学术级验证

---

## 8. 待回答问题（需要本地原型实验）

> 调研工作流标记为 open question，本地实现时要先回答。

1. **Riskfolio-Lib 哪个工作流更适配**：rolling 63d covariance 的 Risk Parity vs Black-Litterman with CAPE-implied equilibrium returns？
2. **DSR 怎么算 N**：把规则参数组合 (thresholds × multipliers × signal set) 的笛卡尔积当 trial count N 吗？
3. **我们的 niche 真稀有吗**：找一个把 DCA + CAPE + 风险预算 + 多标的打包的开源项目（可能没有，所以路线图自研合理）。
4. **QDII 怎么把 risk-budgeted 权重映射到 510500/513100/159941 等**：FX、T+2、QDII 额度都影响实际可执行性。

---

## 9. 引用来源（已验证）

### 主要项目仓库
- Riskfolio-Lib — https://github.com/dcajasn/Riskfolio-Lib
- QuantConnect Lean — https://github.com/QuantConnect/Lean
- Lean Portfolio Construction 文档 — https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/portfolio-construction/supported-models
- Lean Risk Management 文档 — https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/risk-management/supported-models
- Lean Interactive Brokers 文档 — https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/brokerages/supported-models/interactive-brokers
- Zipline-reloaded — https://github.com/stefan-jansen/zipline-reloaded
- vectorbt — https://github.com/polakowo/vectorbt
- vectorbt PRO — https://vectorbt.pro/
- Backtrader — https://github.com/mementum/backtrader
- Qlib (Microsoft) — https://github.com/microsoft/qlib
- mlfinlab (Hudson & Thames) — https://github.com/hudson-and-thames/mlfinlab
- ib_insync — https://github.com/erdewit/ib_insync

### 商业参考
- Betterment portfolio — https://www.betterment.com/portfolio/
- Qplix wealth management — https://qplix.com/wealth-management/

### 学术 / 行业方法论
- Lopez de Prado, "The Deflation of Sharpe Ratios", SSRN 2460551 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Lopez de Prado, *Advances in Financial Machine Learning*, Wiley 2018 — https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086

### 数据 / 信号
- CBOE VIX Term Structure — https://www.cboe.com/us/indices/dashboard/vixtermstructure/
- New York Fed, "The Liberty Street Economics" — https://www.newyorkfed.org/newsevents/speeches/2023/111510-labliberty-streeteconomics
- Federated Hermes, "McClellan Summation Index" — https://www.federatedhermes.com/us/investment-professionals/insights/mcclellan-summation-index.htm
- AAII Sentiment Survey — https://www.aaii.com/sentimentsurvey/sent_results

---

## 10. 调研工作流元数据

- **agent 调用数**：105
- **搜索角度**：5
- **来源抓取**：23 个 URL（去重 1）
- **可证伪断言**：87
- **对抗验证**：25 条（每条 3 票）
- **通过 / 反驳**：15 / 10
- **合成后聚类**：8 个发现
- **预算丢弃**：6 条
- **总耗时**：~47 分钟

### 调研注意事项（用户应知）
- 三个 2-1 票通过（Qlib 范式、DSR 公式）证据比 3-0 弱一档，使用时需自行二次确认。
- WebSearch API 在对抗验证时偶发错误，部分断言只用主仓库 + 官方文档验证，未跨引用学术/行业博客。
- "活跃/停摆"判断（Backtrader、mlfinlab、vectorbt v1.0.0 日期）是 2026-06-06 当下的快照；6-12 个月后会失效，做长期决策时需重查。
- Riskfolio-Lib 风险度量个数（22 vs 37）README 和官方文档略有出入，以官方文档为准。
- mlfinlab 公开仓库自 2021-12-01 起只读，**付费商业分发渠道可能仍活跃**——"已停摆"结论只对**公开仓库**成立。
