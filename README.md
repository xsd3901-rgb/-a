# A-Quant：A股短期波段量化研究系统

A-Quant 是按既定架构开发的 沪深 A 股为主的短期波段量化研究系统。

当前代码版本：`1.0.0-rc1`。这表示功能链路已经进入本地验收候选阶段；在目标电脑完成真实沪深全市场建库和验收之前，不标记为最终 `1.0.0`。

当前主线：

- 日线级沪深 A 股扫描，默认排除北交所和 B 股；
- 持股周期由信号、风险和趋势变化动态决定，“约一周到 30 天”是研究观察范围，不是硬性选股条件；
- 优先使用免费数据源，主源失败后自动降级；
- 本地 Parquet + DuckDB 数据底座；
- 首次建库后按最近真实交易日做增量更新；
- 原始未复权行情保留，复权行情和复权因子分别管理；
- 根据电脑总内存和当前可用内存自动决定批次与资源档位；
- 选股、数据、回测、评估、参数优化和网页端分层，不随意推倒架构。

> 当前已提供东方财富 V1 参考公式导出；由于东方财富公式无法完整表达历史 ST、停牌、点时涨跌停和真实成交约束，正式研究结果仍以 A-Quant 本地系统为准。

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

项目保留多层离线测试，覆盖数据底座、历史股票池、停牌/ST/涨跌停/T+1、点时连续价格、特征验证、Walk-Forward、交易成本、有限资金组合、逐日盯市、V1/V2 对照、数据审计、首次准备流程、网页端和主流程。

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
- 沪深 A 股股票按 T+1 执行：买入当日即使触发止损/止盈，也不会假设当天卖出；
- 停牌日期保留在独立执行时间轴中，用于阻断入场/退出，但不会作为一根“平盘K线”混入 MA、动量等技术指标窗口；
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

特征研究也支持断点续跑。每个已经安全写入 Parquet 分块的股票会记录到：

```text
data_store/research/feature_validation_checkpoint.json
```

同一研究日期再次运行时会跳过已经完成且对应分块仍存在的股票；分块文件被删除时不会盲信检查点。使用 `--refresh` 会重新建立研究样本。

全市场验证不会一次把所有特征列全部装进内存，而是按特征逐列从 Parquet/DuckDB 读取。验证结果只用于决定下一轮应该保留、淘汰或继续观察哪些特征，当前阶段**不会自动修改正式选股评分权重**。


## V2 候选评分与 Walk-Forward

正式评分 V1 暂时保持不变。V2 采用“先验证、再候选、再滚动样本外”的升级流程，任何一步没有通过都不会自动替换当前正式评分。

先运行特征有效性验证：

```powershell
python main.py features --limit 200
```

然后从 5 / 10 / 20 日验证结果中筛选至少两个预测窗口方向一致、样本数量充足且具有统计证据的特征：

```powershell
python main.py select-features
```

候选清单输出：

```text
reports/feature_selection_v2.csv
data_store/research/models/score_v2_candidate.json
```

最后运行滚动样本外验证：

```powershell
python main.py walk-forward --horizon 10
```

默认使用约 252 个交易日训练、63 个交易日验证，并在训练与验证之间保留至少等于预测窗口的 gap，避免未来收益标签跨越边界造成泄漏。每一折都重新只用该折训练期拟合 V2 规则，再应用到后续验证期。

输出：

```text
reports/walk_forward_folds.csv
reports/walk_forward_rules.csv
reports/walk_forward_summary.csv
```

即使 Walk-Forward 基础检查通过，系统目前也只标记为“可进入正式回测候选”，**不会自动切换 V1 → V2**。下一阶段需要把 V2 放入带停牌、ST、涨跌停和真实成交约束的交易回测中，与 V1 做同区间对照后才能决定是否晋级。


## 本地建库与完整验收

当前版本已经把“行情底座 → 点时研究价格 → 特征验证 → Walk-Forward → V1/V2 真实成交对照 → 有限资金组合 → 网页端”串成完整主流程。

首次在本机建立沪深 A 股数据库：

```powershell
python main.py bootstrap
```

先小范围检查也可以：

```powershell
python main.py bootstrap --limit 50
```

默认向前建立约 1300 个自然日的数据。Raw 未复权数据作为成交底稿；QFQ 只保留给当前扫描兼容。历史研究正式优先使用未复权日线中的逐日 `pct_change` 构造 **point-in-time continuous** 连续研究价格，技术指标用连续价格，实际买卖/涨跌停/停牌判断始终使用未复权价格，从而避免把查询时点的前复权历史直接当成过去当时可见的价格。

完整研究验收：

```powershell
python main.py validate-system --limit 200 --horizon 10
```

全市场数据准备充分后，可把 `--limit 200` 改大或按需要运行各单独模块。验收链路包括：

```text
特征有效性
  ↓
V2 候选特征
  ↓
Walk-Forward
  ↓
V1 / V2 同区间真实成交对照
  ↓
V1 正式回测
  ↓
有限资金组合约束
```

V2 即使通过候选检查，也不会自动替换 V1。正式策略参数优化仍由：

```powershell
python main.py optimize --limit 100
```

独立执行，并且只有训练/验证均满足稳健性条件时才更新活动参数。参数优化会改变活动策略参数，因此现在还额外要求本地数据已经通过正式点时数据审计和历史生命周期股票池检查；降级数据不会被允许更新正式参数。

## 回测价格与交易成本

历史信号和实际成交已经分层：

- 技术指标：使用无未来信息的连续研究价格；
- 买入/卖出：使用未复权 OHLC；
- 涨跌停：使用未复权昨收和历史 ST / 板块制度；
- 停牌：使用点时 `trade_status`；
- 交易成本：佣金、过户费、卖出印花税和滑点；
- 组合模拟：有限初始资金、最大持仓数、单股仓位上限、A 股 100 股整数手，并用本地未复权日线逐日盯市计算组合权益和最大回撤。

默认券商佣金是研究假设（万 2.5、最低 5 元），可以在 `config.py` 修改。印花税和过户费按历史制度日期处理；组合模拟使用实际成交金额计算最低佣金，独立单股回测在不知道持仓金额时使用比例化成本估计。

## 网页终端

运行：

```powershell
python webapp.py
```

打开：

```text
http://127.0.0.1:5000
```

网页端现在包含：

- 建立 / 更新本地数据库；
- 沪深 A 股扫描；
- V1 真实成交回测；
- 特征有效性验证；
- V2 候选生成；
- Walk-Forward；
- V1 / V2 对照；
- 一键系统验收；
- 参数优化；
- 本地报告与资源状态。

重任务采用后台线程运行，网页会轮询任务状态，同一时间只运行一个重任务，避免同时抢占内存和数据文件。


## 单股详情与东方财富公式

单股当前量化详情：

```powershell
python main.py stock 600000
```

导出与 V1 主要评分逻辑对应的东方财富参考公式：

```powershell
python main.py formula
```

文件写入：

```text
reports/eastmoney_formula_v1.txt
```

公式包含 V1 的均线、MACD、KDJ、RSI、量比、20 日位置、5/20 日涨幅、过热惩罚以及基础流动性/波动风险闸门。东方财富公式不能完整表达历史 ST、停牌、上市初期无涨跌停、点时成交约束等逻辑，因此实际研究结果仍以 A-Quant 本地系统为准。

## 独立风险闸门

高评分不能绕过风险层。当前日线风险闸门会独立检查：

- 当前交易日是否可交易；
- 点时 ST / 风险警示状态；
- 5 日平均成交额是否低于研究下限；
- ATR 日线波动是否达到极端风险阈值。

风险阈值在 `config.py` 中集中配置。扫描与历史回测使用同一风险逻辑，避免出现“扫描过滤了、回测却没过滤”的口径差异。


### 逐日盯市组合权益

组合层不再只按买入成本记账。实际接受的交易会用本地 `none` 未复权日线逐日估值：

- 买入/卖出现金流继续按滑点、佣金、印花税和过户费处理；
- 持仓市值按当日未复权收盘价计算；
- 停牌或当日无新价格时沿用最近一个可见收盘价；
- 输出 `最大盯市回撤%` 和 `盯市覆盖率%`；
- V1 / V2 晋级对照优先使用逐日盯市回撤，而不是旧的账面成本回撤；
- 盯市只读已经建立的本地行情库，不会为了算回撤额外触发网络请求。

因此有限资金组合层现在同时约束“能买多少”和“持有期间实际经历了多大净值波动”。


## 本地数据完整性审计

正式回测和系统验收之前，可以先检查本地行情库是否完整：

```powershell
python main.py audit-data
```

该命令**只读取本地文件，不访问网络**，会检查：

- 历史股票池中是否有整只股票行情文件缺失；
- 未复权日线是否为空或损坏；
- 完整研究窗口的交易日覆盖率与数据滞后；
- 是否存在重复日期；
- `pct_change`、昨收、交易状态、历史 ST 等点时字段覆盖率；
- 是否存在 QFQ 扫描缓存；
- 正式验收是否使用了历史 `security_lifecycle`，而不是当前股票列表降级替代。

正式验收对点时字段采用更严格的闸门：可交易记录中的 `pct_change`、昨收和历史 ST，以及完整执行时间轴中的交易状态，默认都要求接近完整覆盖（核心点时字段至少 95%）。达不到要求会直接标记为 FAIL，而不是悄悄用降级数据冒充正式回测。

输出：

```text
reports/data_audit.csv
reports/data_audit_summary.csv
```

`validate-system` 现在会先执行这一步。若本地行情出现明显缺口或滞后，会先要求修复/补库，不再带着坏数据继续做策略验收。


## 发布 / 本地运行就绪检查

项目代码下载到本机后，可以先运行：

```powershell
python main.py readiness
```

它不会访问外网，也不会自动修改策略，只检查四层状态：

- 项目核心文件是否齐全；
- Python 核心依赖是否齐全；
- 本地未复权行情库是否通过完整性审计；
- 系统研究验收是否已经完成。

输出：

```text
reports/release_readiness.csv
reports/release_readiness.json
```

典型状态：

```text
代码已就绪，等待本地建库
数据已就绪，等待正式验收
本地运行链路已就绪
```

因此项目交付后不需要靠猜测判断“现在能不能正式跑”，终端会直接告诉你还缺哪一步。


## Windows 首次运行与完整准备

完整项目放到本机后，Windows 用户优先双击：

```text
首次运行.bat
```

它会完成 Python/依赖/代码加载检查，执行一次本地就绪检查，然后启动网页终端。它**不会在你不知情的情况下自动跑几千只股票建库**。

第一次打开网页后，可以点击：

```text
首次完整准备
```

该按钮按顺序执行：

```text
沪深全市场本地建库
  ↓
本地数据完整性审计
  ↓
200只研究样本的特征/WF/V1-V2/回测验收
  ↓
最终就绪检查
```

如果希望不用网页、直接在黑色窗口执行同一套流程，可以双击：

```text
全市场建库并验收.bat
```

或命令行运行：

```powershell
python main.py prepare-local --bootstrap-limit 0 --validation-limit 200 --horizon 10
```

其中 `--bootstrap-limit 0` 表示沪深全市场；研究验收默认先用 200 只控制首次耗时和免费接口压力。全部流程通过以后，再根据本地数据情况扩大验证范围。

准备过程会写出：

```text
reports/local_prepare_steps.csv
reports/local_prepare_summary.json
reports/release_readiness.csv
reports/release_readiness.json
```

之后日常使用只需要双击 `启动量化.bat`。


### 空的 6688 文件夹怎么开始

如果 `C:\\Users\\Lenovo\\Desktop\\6688` 还是空的，最终交付时只需要先放入：

```text
安装量化项目.bat
```

双击后它会从 GitHub 下载当前完整项目到该文件夹。脚本会保留系统代理设置、保留已有 `data_store` / `reports`，下载失败时窗口不会一闪而过。安装完成后会自动进入 `首次运行.bat`。

之后的日常使用顺序是：

```text
首次运行.bat          # 第一次：检查 Python/依赖并打开网页
首次完整准备          # 网页按钮：全市场建库 -> 审计 -> 验收
启动量化.bat          # 以后日常直接启动网页终端
```

源码 ZIP 下载本身不会触发 GitHub Actions；项目的 Actions 工作流保持手动触发。


### RC1 本地验收条件

`1.0.0-rc1` 的代码层已经收口。只有目标电脑上的真实沪深数据同时满足下面条件，才适合把版本从 RC 提升为最终 `1.0.0`：

```text
历史 security_lifecycle 可用
+ 全市场 none 未复权行情建库完成
+ 点时字段审计通过
+ 特征/WF/V1-V2 链路通过
+ 正式回测与逐日盯市组合可复现
+ readiness = 本地运行链路已就绪
```

因此“代码测试全部通过”和“真实市场数据验收完成”是两件事；系统不会把前者冒充后者。


## 全市场建库性能与断点续跑

RC1 之后的第一轮性能优化已经加入全市场建库主流程：

- **真正增量更新**：本地已有历史时，只下载缺失的首段/尾段，不会因为最新少一天就重新抓取整个 1300 天窗口；
- **断点续跑**：`reports/bootstrap_checkpoint.json` 记录同一市场日期已经完成的股票。断网、关机或手动中断后再次运行，会继续未完成股票；
- **检查点防误跳过**：即使检查点显示完成，只要对应未复权/QFQ 文件被删除，就会重新处理；
- **有限自动重试**：单股免费接口临时失败时自动重试，失败股票继续写入 `bootstrap_errors.csv`；
- **资源自适应并发**：根据可用内存档位选择线程数，并受 `bootstrap_max_workers` 安全上限控制；
- **数据源并发保护**：BaoStock 串行访问，EastMoney 最多双并发，避免为了追求速度把免费接口打挂；
- **来源追踪**：`reports/data_source_quality.csv` 记录 Raw/QFQ 实际使用的数据源分布；
- **网页实时进度**：建库时网页任务状态会显示已完成、失败和断点跳过数量。

默认配置：

```text
bootstrap_max_workers = 3
bootstrap_retry_attempts = 2
bootstrap_resume = True
```

`--refresh` 会清空当前日期的断点状态并强制重新抓取，因此只在确实需要重建数据时使用。正常每天更新不建议加 `--refresh`。


## 定向修复失败行情

数据审计发现少数股票 FAIL 时，不需要重新抓取全市场：

```powershell
python main.py repair-data
```

只修复前 N 只失败股票：

```powershell
python main.py repair-data --limit 20
```

流程是：

```text
本地 audit-data
  ↓
提取 FAIL 股票代码
  ↓
只对这些股票强制刷新历史数据
  ↓
再次执行全库本地审计
  ↓
输出仍失败股票
```

网页端对应按钮为 **“修复失败数据”**。输出写入：

```text
reports/data_repair.csv
reports/data_repair_summary.json
```

这样代理或免费接口偶发失败时，第二次处理只补坏掉的股票，不必浪费时间重建整个沪深市场。


## 扫描与回测并行计算

全市场建库稳定后，扫描和历史回测也已经接入资源自适应并发：

- 扫描并发取 `compute_workers` 与 `download_workers` 的较小值；
- 回测并发取 `backtest_workers` 与 `download_workers` 的较小值；
- 每个工作线程使用独立 `MarketDataService`，避免共享可变请求状态；
- 底层数据源仍执行 BaoStock 单路、EastMoney 最多双路的安全闸门；
- 结果汇总、CSV 写入都回到主线程，避免多线程同时写同一报告；
- 进度会显示当前处理速度和预计剩余时间；
- 内存较少时资源档位会自动把并发降到 1～2，不强行追求速度。

因此“下载并发”和“指标/回测计算并发”是两层独立约束：本地数据齐全时可充分利用 CPU，而需要补行情时仍保护免费接口。
