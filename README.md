# A-Quant：A股短期波段量化研究系统

A-Quant 是按既定架构开发的 沪深 A 股为主的短期波段量化研究系统。

当前主线：

- 日线级沪深 A 股扫描，默认排除北交所和 B 股；
- 持股周期由信号、风险和趋势变化动态决定，“约一周到 30 天”是研究观察范围，不是硬性选股条件；
- 优先使用免费数据源，主源失败后自动降级；
- 本地 Parquet + DuckDB 数据底座；
- 首次建库后按最近真实交易日做增量更新；
- 原始未复权行情保留，复权行情和复权因子分别管理；
- 根据电脑总内存和当前可用内存自动决定批次与资源档位；
- 选股、数据、回测、评估、参数优化和网页端分层，不随意推倒架构。

> 东方财富公式/指标转换暂不属于当前开发范围，留到策略和终端成熟后的最终落地阶段。

## 当前数据架构

```text
免费数据源
   ↓
安全抓取 / 超时保护 / 主备切换
   ↓
Raw 原始层
   ↓
字段统一 + 质量检查
   ↓
Standard 标准层
   ↓
Parquet 本地文件 + DuckDB 查询目录
   ↓
扫描 / 回测 / 参数优化 / 后续特征工程
```

当前已建立的数据模块包括：

- 股票基础库：`security_master`
- 交易日历：`trade_calendar`
- 个股日线：未复权 / 前复权 / 后复权标准库
- 原始行情留档
- 复权因子事件表：`adjust_factor`
- 主要指数日线：上证、深证、创业板、沪深300、中证500、中证1000
- 行业映射：`industry_map`
- 数据质量检查与主备数据源接口
- 内存自适应资源调度
- 沪深市场环境识别：上证、深证、创业板、沪深300、中证1000
- 日线特征工程：趋势、动量、量能、突破、波动、回撤、相对强弱
- 历史股票生命周期：上市日 / 退市日 / 市场 / 板块
- 日线点时状态：交易状态、昨收、涨跌幅、BaoStock 历史 ST 标记
- A 股交易规则引擎：主板 / 创业板 / 科创板 / 北交所涨跌停制度和新股无涨跌幅阶段

### 历史股票池与交易规则

回测不能只使用“今天仍然上市”的股票，否则会产生幸存者偏差。项目现在增加了 `security_lifecycle`，可以按历史日期生成当时的股票池，并保留退市股票。

沪深日线通过 BaoStock 额外保留 `tradestatus` 和 `isST`，用于历史停牌和 ST 状态判断；交易规则层按代码、日期、历史 ST 状态和上市后的交易日序号决定对应的涨跌停规则。北交所历史生命周期覆盖还需要后续增加独立来源，因此不会伪造缺失历史资料。

本地数据路径已经锚定到**项目根目录**，不会跟 PowerShell 当前目录乱跑。当前电脑把项目放在 `C:\\Users\\Lenovo\\Desktop\\6688` 时，数据库固定为 `C:\\Users\\Lenovo\\Desktop\\6688\\data_store\\aquant.duckdb`。

本地数据结构：

```text
data_store/
├─ raw/
├─ standard/
│  ├─ daily/
│  │  ├─ none/
│  │  ├─ qfq/
│  │  └─ hfq/
│  ├─ reference/
│  ├─ context/
│  └─ adjust_factor/
└─ aquant.duckdb
```

`data_store/` 已加入 `.gitignore`，行情数据库不会提交到 GitHub。

## 主要代码结构

```text
-a/
├─ aquant/
│  ├─ data/
│  │  ├─ providers/              # 数据源适配器
│  │  ├─ schema.py               # 统一字段契约
│  │  ├─ quality.py              # 数据质量与交叉检查
│  │  ├─ storage.py              # 个股日线 Parquet / DuckDB
│  │  ├─ service.py              # 扫描/回测统一数据入口
│  │  ├─ reference.py            # 股票基础库/交易日历存储
│  │  ├─ reference_service.py    # 股票基础库/交易日历更新
│  │  ├─ context_store.py        # 指数/行业存储
│  │  ├─ context_service.py      # 指数/行业更新
│  │  ├─ factor_store.py         # 复权因子存储
│  │  ├─ factor_service.py       # 复权因子服务
│  │  └─ safe_fetch.py           # 免费接口超时隔离
│  └─ runtime/
│     └─ resources.py            # 内存自适应资源调度
├─ scanner.py                    # 全市场扫描
├─ backtest.py                   # 历史回测
├─ evaluator.py                  # 回测评估
├─ optimizer.py                  # 受控参数优化
├─ indicators.py                 # 技术指标
├─ strategy.py                   # 当前基准策略
├─ diagnose.py                   # 环境/真实数据接口诊断
├─ webapp.py                     # 本地网页端
├─ main.py                       # 命令行入口
└─ tests/
   ├─ smoke_data_offline.py      # 数据底座离线冒烟测试
   └─ smoke_pipeline_offline.py  # 扫描/回测/优化离线链路测试
```

根目录旧版 `data.py` 暂时保留作为回滚保险，正式主流程已经使用 `aquant.data.service.MarketDataService`。

## 数据源降级原则

股票基础列表当前按以下顺序尝试：

1. 东方财富 / AKShare；
2. 沪深北交易所列表接口；
3. BaoStock；
4. 已存在的本地股票基础库快照。

历史日线当前为：

1. 东方财富 / AKShare；
2. BaoStock；
3. 本地已经保存的数据。

免费接口都设置了超时隔离，某个网站卡住时不会无限拖死主程序。

## 安装

建议 Python 3.11 / 3.12。

```powershell
python -m pip install -r requirements.txt
```

## 推荐使用顺序

### 1. 环境与真实免费接口诊断

```powershell
python diagnose.py
```

诊断会检查依赖、内存资源档位、股票基础库、交易日历、日线、本地数据库，以及指数/行业数据。

### 2. 小范围扫描

```powershell
python main.py scan --limit 50
```

首次正常后再扫描全市场：

```powershell
python main.py scan
```

### 3. 回测

```powershell
python main.py backtest --limit 100
```

### 4. 受控参数优化

```powershell
python main.py optimize --limit 100
```

查看当前活动参数：

```powershell
python main.py profile
```

### 5. 网页端

```powershell
python webapp.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

## 测试

项目保留两层离线测试：

- 数据底座测试：Parquet、DuckDB、股票基础库、交易日历、日线、行业、指数、复权因子；
- 主流程测试：扫描、回测、参数优化使用模拟行情完整运行。

GitHub Actions 冒烟测试已改成**手动触发**，不会每次提交代码都自动运行，避免无意义消耗 Actions 免费额度。

## 当前基准策略

第一版可解释规则模型目前包含：

- 趋势：MA5 / MA10 / MA20 / MA60
- 动量：MACD / RSI / KDJ
- 量能：成交量与量比
- 强弱：5 日 / 20 日表现与 20 日高位位置
- 风险：ATR、过热状态
- 退出：ATR 风险线、目标线、趋势转弱、最大观察窗口

这只是后续研究的基准模型。数据底座稳定后，再逐步完善市场环境、风控、时间序列验证和 Walk-Forward。

> 本项目是研究和辅助决策工具，不保证收益。历史回测或样本外验证均不代表未来表现。


### 回测成交真实性

回测现在不再默认“信号出现就一定成交”，而是按沪深 A 股点时状态做基础成交约束：

- 信号日或次日处于历史 ST 状态时，按当前默认设置不新开仓；
- 次日停牌或日线标记不可交易时，不假设可以买入；
- 次日开盘即涨停时，采用保守假设，不把该笔信号当作已成交；
- 持仓期间若遇到一字跌停，不假设可以按止损价或收盘价卖出；
- 观察窗口结束时若当天仍无法卖出，会顺延到下一可卖交易日；
- 数据区间结束仍无法卖出的未完成持仓，不计入已完成交易；
- 历史回测优先使用 BaoStock 的点时 `tradestatus`、`isST`、`preclose`、`pctChg` 字段；
- 回测股票池优先使用上市/退市生命周期资料，而不是只使用今天仍然上市的股票。

默认正式信号区间约为最近 540 个自然日，另取 260 个自然日作为指标预热区间；这两个窗口是回测数据范围，不是持股期限限制。


## 特征有效性验证

架构定型后，新增独立的特征研究流程。它不会直接改正式评分，而是先检查每个候选特征在训练期和验证期是否保持同一方向。

当前验证特征包括：

- 趋势：MA20 位置、MA20/MA60 斜率、均线结构；
- 动量：5/20 日收益与组合动量；
- 突破：20 日前高突破、区间位置；
- 量能：20 日量比、成交额短长周期比；
- 风险：ATR、20 日波动率、20 日回撤；
- 相对强弱：相对沪深300的 20 日表现；
- 布尔条件：趋势向上、短均线多头、接近突破、健康量能、过热。

默认采用时间顺序 70% 训练 / 30% 验证，预测标签为未来 5 / 10 / 20 个交易日收益。连续特征同时输出 Spearman IC、高低 30% 分组收益差和验证期 t 值；布尔特征输出 True/False 两组未来收益差。

运行：

```powershell
python main.py features --limit 200
```

扩大到全部沪深股票时可用：

```powershell
python main.py features --limit 0
```

输出：

```text
reports/
├─ feature_validation.csv
├─ feature_validation_meta.csv
└─ feature_validation_errors.csv   # 仅有错误时生成
```

研究样本会按内存自适应批次写入：

```text
data_store/research/feature_validation/
```

全市场验证不会一次把所有特征列全部装进内存，而是按特征逐列从 Parquet/DuckDB 读取。验证结果只用于决定下一轮应该保留、淘汰或继续观察哪些特征，当前阶段**不会自动修改正式选股评分权重**。
