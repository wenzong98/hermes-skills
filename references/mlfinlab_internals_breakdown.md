# mlfinlab 模块内部逻辑详查

> **目的**：把 deep-research 报告里提到的 "Hudson & Thames mlfinlab" 钻到源码级，看清楚每个模块具体实现了哪些类/函数、对应 Lopez de Prado *Advances in Financial Machine Learning*（AFML）哪些章节/snippet。  
> **范围**：cross_validation、bet_sizing、backtest_statistics、labeling、structural_breaks、feature_importance、sampling、sample_weights、data_structures、features、multi_product、codependence、networks、microstructural_features、regression、ensemble、clustering。  
> **重要前提**：公开仓库 `hudson-and-thames/mlfinlab` 自 2021-12-01 起没有代码提交，LICENSE 是 "all rights reserved"，完整库通过付费商业 license 分发。下面是**公开源码**能看到的部分——付费版可能还有更多（filter、filters/、multi_product/、networks/ 等更深层模块未在公开页展开）。

---

## 1. 总览：每个模块干一件事

mlfinlab 的设计哲学是 **1 模块 = Lopez de Prado 一章**。下表把每个模块的公开文件 / 类 / 函数映射到 AFML 章节。

| 模块 | 对应 AFML 章节 | 公开文件 | 核心类/函数 | 用途 |
|---|---|---|---|---|
| `data_structures` | 第 2-3 章 | base_bars / standard_data_structures / imbalance_data_structures / time_data_structures | Dollar bars、Tick bars、Volume bars、EMA imbalance bars、CMD imbalance bars、Run bars、Time bars | 把 tick 重新聚合成信息驱动的 bars（避免时间 bars 的低质采样） |
| `labeling` | 第 3 章 | fixed_time_horizon / trend_scanning / tail_sets / raw_return / return_vs_benchmark / matrix_flags / excess_over_mean / excess_over_median / bull_bear / labeling | (各文件对应一个 labeler) | 给 raw returns 加 label：固定窗口、三重屏障（trend_scanning 是其代理）、尾部分位、超额均值/中位、牛熊标签 |
| `sample_weights` | 第 4 章 | attribution | `SampleWeights` (基于 label overlap 唯一性) | 给每条样本一个 0-1 权重（重叠越多权重越低，纪避免 look-ahead bias 放大重复观察） |
| `cross_validation` | 第 7 章 | cross_validation / combinatorial | `PurgedKFold`、`StackedPurgedKFold`、`CombinatorialPurgedKFold`、`StackedCombinatorialPurgedKFold`、`ml_cross_val_score`、`ml_get_train_times` | 时间序列专用的 K-Fold，**purge** 重叠 + **embargo** 隔墙；CPCV 是 Lopez de Prado 第 12 章的实现 |
| `bet_sizing` | 第 10 章 | bet_sizing / ef3m / ch10_snippets | `bet_size_probability`、`bet_size_dynamic`、`bet_size_budget`、`bet_size_reserve`、`cdf_mixture`、`get_concurrent_sides`、`single_bet_size_mixed` | 把分类概率 → 仓位大小（4 个 sizer）；配合 meta-labeling 流程 |
| `structural_breaks` | 第 17 章 | chow / cusum / sadf | `get_chow_stat`、`get_cusum_stats`、`get_sadf` | Chow 已知断点、CUSUM 递归残差累积、SADF (Phillips-Shi-Yu) 多次泡沫检测 |
| `backtest_statistics` | 第 14 章 | statistics / backtests | `sharpe_ratio`、`information_ratio`、`probabilistic_sharpe_ratio`、`deflated_sharpe_ratio`、`minimum_track_record_length`、`timing_of_flattening_and_flips`、`average_holding_period`、`bets_concentration`、`all_bets_concentration`、`drawdown_and_time_under_water` | 算 Sharpe、PSR、DSR、最短回测长度、HHI 集中度、回撤 + 水下时间 |
| `features` | 第 5、6 章 | fracdiff | `fracdiff_ffd` / `fracdiff_series` (FFD 分数差分) | 把非平稳序列做分数阶差分，保留 memory 又平稳 |
| `feature_importance` | 第 8 章 | importance / orthogonal / fingerpint(typo) | (未展开) | MDI、MDA、SFI、PCA orthogonalization |
| `sampling` | 第 4 章 | bootstrapping / concurrent | (未展开) | Sequential bootstrap、Monte Carlo、concurrent-shuffle |
| `multi_product` | 第 5 章 | (未公开) | — | 多标的矩阵特征 (e.g. ETF 间相关) |
| `codependence` | 第 6 章 | (未公开) | — | 距离/相关：Pearson、Spearman、Kendall、tail-dependence、copula 距离 |
| `networks` | 第 6 章 | (未公开) | — | MST、PMFG、ONCO 金融网络 |
| `microstructural_features` | 第 1 章 | (未公开) | — | 知情交易概率、VPIN、order flow imbalance |
| `regression` | (scikit-learn wrapper) | (未公开) | — | 用 sample_weights/cv 跑回归 |
| `ensemble` | 第 8 章 | (未公开) | — | 多模型 bagging/weighting |
| `clustering` | 第 6 章 | (未公开) | — | ONC、k-means、HRP/HERC 输入用 |

---

## 2. 关键模块的具体类 / 函数签名

### 2.1 `cross_validation/`（时间序列 K-Fold + CPCV）

**`cross_validation.py`**
```python
class PurgedKFold(KFold):
    def __init__(self, n_splits=3, samples_info_sets=None, pct_embargo=0.):
        # samples_info_sets: 每条样本的 [t0, t1] 区间
        # pct_embargo: test 段后额外禁用的样本比例
        ...

class StackedPurgedKFold(KFold):
    # 多资产版的 PurgedKFold
    ...

def ml_get_train_times(samples_info_sets, test_times):
    # AFML Snippet 7.1, p.106 — 把与 test label 区间重叠的训练样本 purge 掉
    ...

def ml_cross_val_score(classifier, X, y, cv_gen,
                       sample_weight_train=None, sample_weight_score=None,
                       scoring=log_loss, require_proba=True, n_jobs_score=1):
    # AFML Snippet 7.4, p.110 — 带 sample weight 的 CV
    ...

def stacked_ml_cross_val_score(...):  # 多资产版
    ...
```

**`combinatorial.py`**
```python
def _get_number_of_backtest_paths(n_train_splits, n_test_splits):
    # 返回 C(N, K) — CPCV(N, K) 的路径数
    ...

class CombinatorialPurgedKFold(KFold):
    def __init__(self, n_splits, n_test_splits, samples_info_sets, pct_embargo):
        # N = n_splits（总 fold 数）, K = n_test_splits（每次 test 包含的连续 fold 数）
        ...
    def split(self, X, y, groups):
        # 产生 C(N, K) 条 backtest path
        ...

class StackedCombinatorialPurgedKFold(KFold):
    # 多资产版的 CPCV
    ...
```

**关键点**：
- 公开版本**不包含** `computePBO`（probability of backtest overfitting），那部分在 Lopez de Prado 第 12 章的 CSCV 流程里是单独代码，付费版可能有。
- 公开 `cross_validation.py` **不包含** walk-forward / expanding window——只有 K-Fold-base purged scheme。walk-forward 你要自己写。

### 2.2 `bet_sizing/`（4 个 sizer + 工具）

```python
def bet_size_probability(prob, num_classes=2, pred=None, step_size=0.0,
                         average_active=False):
    # 把分类概率 p 转成 0-1 之间的仓位
    # 与 AFML 第 10 章配套，配合 meta-labeling（二次分类器）
    ...

def bet_size_dynamic(prob, num_classes=2, pred=None, step_size=0.0,
                     average_active=False, signal=None,
                     max_position=1, commission=0.0):
    # 含 max_position / 限价单的动态仓位
    # 持仓受 sigmoid 或 power 关系约束
    ...

def bet_size_budget(active_long, active_short, ...):
    # AFML Section 10.2 — 同时多空仓位的线性配权
    # 仓位大小 = t 时刻净敞口 / 多空总敞口
    ...

def bet_size_reserve(returns, ...):
    # 用 2-Gaussian 混合拟合 t 时刻多空不平衡 → sigmoid 仓位
    # 这是 AFML 10.4 / Question 10.4 的解
    ...

def cdf_mixture(x, mus, sigmas, weights):
    # 2-Gaussian 混合的 CDF（被 bet_size_reserve 内部用）
    ...

def get_concurrent_sides(target_positions):
    # 统计每个时间点 active 的多 / 空笔数（被 bet_size_reserve / bet_size_budget 用）
    ...
```

**关键点**：
- **没有** 经典 Kelly 实现。`bet_size_reserve` 是 Lopez de Prado 的 **reserve-based sigmoid**（一种 fractional Kelly 替代品），不是 full Kelly。
- 配合 meta-labeling（AFML 第 3 + 第 10 章）：primary model 出 side（二次分类），secondary model 出 prob，再用 `bet_size_probability` 转 size。

### 2.3 `backtest_statistics/statistics.py`（DSR / PSR / MinTRL）

```python
def sharpe_ratio(returns, entries_per_year=252, risk_free_rate=0): ...

def information_ratio(returns, benchmark=0, entries_per_year=252): ...

def probabilistic_sharpe_ratio(observed_sr, benchmark_sr, number_of_returns,
                               skewness_of_returns=0, kurtosis_of_returns=3):
    # PSR — 真实 SR > benchmark 的概率，校正 skew/kurtosis
    # AFML 第 5 章
    ...

def deflated_sharpe_ratio(observed_sr, sr_estimates, number_of_returns,
                          skewness_of_returns=0, kurtosis_of_returns=3,
                          estimates_param=False, benchmark_out=False):
    # DSR — 考虑 N 个 trial 之后的"调整后" Sharpe significance
    # sr_estimates: 历次 trial 的 SR 列表（用来估计 E[max SR]）
    # number_of_returns: T（样本数）
    # AFML 第 5 章 + SSRN 2460551
    ...

def minimum_track_record_length(observed_sr, benchmark_sr,
                                skewness_of_returns=0, kurtosis_of_returns=3,
                                alpha=0.05):
    # MinTRL — 在给定的 SR / skew / kurtosis 下，达成 statistical significance 所需的最少观测数
    # AFML 第 14 章
    ...

def timing_of_flattening_and_flips(target_positions):  # AFML Snippet 14.1
def average_holding_period(target_positions):  # AFML Snippet 14.2
def bets_concentration(returns):  # AFML Snippet 14.3 — HHI 集中度
def all_bets_concentration(returns, frequency='M'):  # AFML Snippet 14.3
def drawdown_and_time_under_water(returns, dollars=False):  # AFML Snippet 14.4
```

**关键点**：
- 这是 **DSR / PSR 的标准实现**，AFML 第 5 章 + Lopez de Prado, "The Deflation of Sharpe Ratios" (SSRN 2460551)。  
- 注意 DS_R 的输入 `sr_estimates` 必须是 **N 个 trial 的 SR 列表**——你需要自己把"信号阈值 × multiplier × 周期"的所有变体跑一遍，把所有 SR 收集起来当输入。
- **没有** Probability of Backtest Overfitting (PBO) 的实现——CSCV/CPCV-PBO 流程在付费版或者要自己写。
- **没有** 显式的 SR 置信区间（用 PSR 替代）。

### 2.4 `labeling/`（9 种 labeler）

| 文件 | Labeler | 用途 |
|---|---|---|
| `fixed_time_horizon.py` | `FixedTimeHorizon` | t→t+h 的 raw return |
| `trend_scanning.py` | `TrendScan` | AFML 第 3.5 节：多 horizon 多 slope 的回归显著性检验，作为 triple-barrier 的代理 |
| `raw_return.py` | `RawReturn` | 同 fixed-time-horizon 的退化版 |
| `return_vs_benchmark.py` | `ReturnVsBenchmark` | 对 benchmark 的超额收益 |
| `excess_over_mean.py` | `ExcessOverMean` | 超均值的离散 label |
| `excess_over_median.py` | `ExcessOverMedian` | 超中位 |
| `tail_sets.py` | `TailSets` | 上尾/下尾分位 label（用于分位策略） |
| `bull_bear.py` | `BullBear` | 多空状态 label |
| `matrix_flags.py` | `MatrixFlags` | 矩阵化处理多 barrier 组合 |
| `labeling.py` | `Labeling` (核心) | **包含 triple-barrier 主体逻辑**（AFML 第 3.3 节） |

**关键点**：
- 真正的 triple-barrier 在 `labeling.py` 主文件（不是 `triple_barrier.py`），参数：`t_final` (上限时间)、`upper`/`lower` barrier、`side` (多空)、`min_ret` (门槛)。
- `trend_scanning` 是 **triple-barrier 的连续版本**，适合波动率 regime。
- `meta-labeling` 不在 `labeling/` 里，是用 `bet_sizing.bet_size_probability` + primary model + secondary model 拼出来的。

### 2.5 `structural_breaks/`（3 个 test）

```python
# chow.py — 已知断点的 Chow test
def get_chow_stat(series, test_indices):
    # 已知断点 t, F 检验 (restricted vs. unrestricted regression)
    ...

# cusum.py — Brown-Durbin-Evans CUSUM
def get_cusum_stats(series, market_data=None, threshold=0.05):
    # 递归残差累积和，跨过 ±threshold 视为 structural break
    # AFML Chapter 2 / 17
    ...

# sadf.py — Supremum Augmented Dickey-Fuller (Phillips-Shi-Yu 2015)
def get_sadf(series, model='c', lags=1, min_window=20, max_window=None):
    # 检验"sup F" 是否有 explosive 行为，识别多次泡沫起止
    ...
```

**关键点**：没有 Bai-Perron 多断点检验；多断点要自己实现或者用 ruptures/pyruptures。

### 2.6 `features/fracdiff.py`（分数差分）

```python
def fracdiff_ffd(series, d, thres=1e-5):
    # Fixed-Width Window Fracdiff (FFD)
    # 保留 series 全部 memory 但达到平稳
    # d 越大 → 越接近一阶差分；d=0 → 原序列
    # AFML 第 5 章
    ...

def fracdiff_series(series, d):
    # 标准 fracdiff（可能生成完整长度的 weights，比 FFD 慢但精确）
    ...
```

**对我们的项目意义**：ETF 月度收益率常带"spurious memory"（CAPE 比率、QQQ/SPY 比率等），用 fracdiff 可以构造更稳的均值回归/动量特征。

### 2.7 `data_structures/`（信息驱动 bars）

```python
# standard_data_structures.py
def get_dollar_bars(file_path, threshold, ...): ...
def get_volume_bars(file_path, threshold, ...): ...
def get_tick_bars(file_path, threshold, ...): ...

# imbalance_data_structures.py
def get_ema_imbalance_bars(file_path, expected_imbalance_window, ...):
    # EMA of signed order flow, threshold breach → bar
    ...
def get_cmd_imbalance_bars(file_path, window=...):
    # Cumulative mean of (buy - sell) order imbalance
    ...

# time_data_structures.py
def get_time_bars(file_path, ...): ...
```

**关键点**：这一整套是为 tick / 限价订单簿（LOB）数据设计的。我们用日级 SPY/QQQ 指数数据**用不上**——是给 HFT 或 order book 重采样用的。

### 2.8 `feature_importance/`（3 个文件）

```python
# importance.py
def mean_decrease_impurity(clf, X, y): ...      # MDI (in-sample)
def mean_decrease_accuracy(clf, X, y, cv): ...   # MDA (cross-validated)
def single_feature_importance(clf, X, y): ...    # SFI

# orthogonal.py
def get_orthogonal_features(X, y): ...           # PCA orthogonalization

# fingerpint.py  (注意：原拼写错误 fingerpint 而非 fingerprint)
def feature_fingerprint(X, y): ...                # 用 copula 测 feature stability over time
```

### 2.9 `sampling/`（2 个文件）

```python
# bootstrapping.py
def get_sequential_bootstrap(ind_matrix, compare_length=...):
    # Sequential bootstrap (AFML Chapter 4 Snippet 4.5) — 解决 IID bootstrap 高估样本多样性
    # 用 ind_matrix 算样本唯一性，贪心选下一个 epoch 让新样本均摊尚未被抽过的 "uniqueness 槽位"
    ...

def get_ind_matrix(samples_info_sets, price_bars): ...  # ind_matrix = t×t 唯一性矩阵

# concurrent.py
def get_concurrent_bootstrap_labels(label_endpoints, ...): ...  # 多资产并发 bootstrap
```

### 2.10 `sample_weights/attribution.py`

```python
def get_weights_by_returns(returns, ...): ...           # 用 1/abs(returns) 作 weight
def get_weights_by_time_decay(returns, ...): ...        # 时间衰减 weight
def get_weights_by_avol(events, num_threads=...): ...   # 用 average uniqueness (AFML 4.5.2)
def get_weights_by_corr(returns, ...): ...              # 用 cluster 之后的 concentration
```

---

## 3. 我们项目能直接借鉴的具体逻辑

| 我们的需求 | mlfinlab 提供 | 复刻难度 | 来源 |
|---|---|---|---|
| DSR 报告 | `deflated_sharpe_ratio(observed_sr, sr_estimates, T, skew, kurt)` | 低（~50 行 Python 复刻，函数签名就是输入） | `backtest_statistics/statistics.py` |
| PSR | `probabilistic_sharpe_ratio(...)` | 低 | 同上 |
| MinTRL（最少回测长度） | `minimum_track_record_length(...)` | 低 | 同上 |
| Bet concentration HHI | `bets_concentration(returns)` / `all_bets_concentration(returns, 'M')` | 低 | 同上 |
| 算 bets 平均持有期 | `average_holding_period(target_positions)` | 低 | 同上 |
| 时间序列 K-Fold（purge + embargo） | `PurgedKFold` | 中 | `cross_validation/cross_validation.py` |
| CPCV 产生多条 backtest path | `CombinatorialPurgedKFold.split()` | 中 | `cross_validation/combinatorial.py` |
| Trend-scanning label | `TrendScan` | 中 | `labeling/trend_scanning.py` |
| 分数差分构造稳态特征 | `fracdiff_ffd(series, d, thres)` | 低 | `features/fracdiff.py` |
| 1d regime tag（SADF bubble 测试） | `get_sadf(series)` | 低-中 | `structural_breaks/sadf.py` |
| 已知 regime 断点检验 | `get_chow_stat(series, t)` | 低 | `structural_breaks/chow.py` |
| CUSUM 实时 regime 监测 | `get_cusum_stats(series)` | 低-中 | `structural_breaks/cusum.py` |

## 4. 我们项目**不会**用到的逻辑

| 类别 | 原因 |
|---|---|
| `data_structures`（dollar bars / imbalance bars） | 我们用日级 ETF 收盘价，零 LOB / tick |
| `microstructural_features` | 同上 |
| `networks`（MST/PMFG） | 我们标的就 SPY+QQQ（+ 未来 TLT 等），网络结构无意义 |
| `codependence`（copula 距离） | 3-5 标的不值得用 copula |
| `bet_sizing` 的 4 个 sizer | 我们是 DCA 固定金额，不是 model-predicted 仓位；meta-labeling 整个范式不适用 |
| `clustering` | 3-5 标的 cluster 是杀鸡用牛刀 |
| `feature_importance`（MDI/MDA/SFI） | 我们是规则化，无 ML 特征重要性问题 |
| `regression`、`ensemble` | 我们无 ML |

## 5. 一个核心判断

**mlfinlab 80% 的模块是给"基于 ML 的" quant pipeline 服务的**（ML 预测 + meta-labeling + ML 仓位 + ML 评估 + ML 特征）。我们项目是**纯规则 DCA + 风险预算**，能直接借鉴的只有 4-5 个文件：  
- `backtest_statistics/statistics.py`（DSR / PSR / MinTRL / HHI）  
- `cross_validation/`（PurgedKFold / CPCV）  
- `structural_breaks/`（SADF / CUSUM / Chow）  
- `features/fracdiff.py`（如果想加 fracdiff 特征）  
- `labeling/trend_scanning.py`（如果想做趋势型 label）

其余模块跟我们系统**架构不兼容**——这部分代码的复刻价值是**算法逻辑**（公式和数值实现），而不是作为一个依赖项引入我们的系统。这就是为什么 deep-research 报告里说"**不要把 mlfinlab 作为依赖**"：商业授权 + 公开仓库停摆 + 我们 80% 用不上。

---

## 6. 引用来源

- [hudson-and-thames/mlfinlab (GitHub)](https://github.com/hudson-and-thames/mlfinlab) — 公开仓库根
- [mlfinlab/cross_validation/ (GitHub tree)](https://github.com/hudson-and-thames/mlfinlab/tree/master/mlfinlab/cross_validation)
- [mlfinlab/cross_validation/cross_validation.py (raw)](https://raw.githubusercontent.com/hudson-and-thames/mlfinlab/master/mlfinlab/cross_validation/cross_validation.py)
- [mlfinlab/cross_validation/combinatorial.py (raw)](https://raw.githubusercontent.com/hudson-and-thames/mlfinlab/master/mlfinlab/cross_validation/combinatorial.py)
- [mlfinlab/bet_sizing/bet_sizing.py (raw)](https://raw.githubusercontent.com/hudson-and-thames/mlfinlab/master/mlfinlab/bet_sizing/bet_sizing.py)
- [mlfinlab/backtest_statistics/statistics.py (raw)](https://raw.githubusercontent.com/hudson-and-thames/mlfinlab/master/mlfinlab/backtest_statistics/statistics.py)
- [mlfinlab/labeling/ (GitHub tree)](https://github.com/hudson-and-thames/mlfinlab/tree/master/mlfinlab/labeling)
- [mlfinlab/structural_breaks/ (GitHub tree)](https://github.com/hudson-and-thames/mlfinlab/tree/master/mlfinlab/structural_breaks)
- [mlfinlab/features/ (GitHub tree)](https://github.com/hudson-and-thames/mlfinlab/tree/master/mlfinlab/features)
- [mlfinlab/feature_importance/ (GitHub tree)](https://github.com/hudson-and-thames/mlfinlab/tree/master/mlfinlab/feature_importance)
- [mlfinlab/sampling/ (GitHub tree)](https://github.com/hudson-and-thames/mlfinlab/tree/master/mlfinlab/sampling)
- [mlfinlab/sample_weights/ (GitHub tree)](https://github.com/hudson-and-thames/mlfinlab/tree/master/mlfinlab/sample_weights)
- [mlfinlab/data_structures/ (GitHub tree)](https://github.com/hudson-and-thames/mlfinlab/tree/master/mlfinlab/data_structures)
- Lopez de Prado, *Advances in Financial Machine Learning*, Wiley 2018 — https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086
