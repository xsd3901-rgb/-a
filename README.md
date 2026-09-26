# A股短期波段量化选股器

这是按既定方案搭建的第一版基础工程，目标是：

- 扫描沪深北 A 股市场，按日线数据进行短期波段选股；
- 持有周期由信号、止损、趋势变化和回测结果决定，“约一周到30天”只作为回测观察范围，不是硬性选股条件；
- 优先使用免费数据源，并保留备用数据源切换能力；
- 每次只处理少量股票/单只股票历史数据，避免一次性把全市场数据装入内存；
- 选股逻辑、数据层、回测、策略评估、网页端彼此分离，后续只迭代模块，不随意改架构；
- 可生成东方财富公式参考稿，便于把核心条件迁移到东方财富 PC 端。

## 目录

```text
-a/
├─ main.py                 # 命令行入口
├─ config.py               # 全局设置
├─ data.py                 # 免费数据源 + 本地缓存
├─ indicators.py           # MA/MACD/KDJ/RSI/ATR/量能等
├─ strategy.py             # 短期波段评分模型
├─ scanner.py              # 全市场扫描
├─ backtest.py             # 动态退出回测
├─ evaluator.py            # 回测统计与策略健康度
├─ eastmoney_formula.py    # 生成东方财富公式参考稿
├─ webapp.py               # 本地网页端
├─ diagnose.py             # 环境/接口诊断
├─ requirements.txt
├─ data_cache/             # 自动创建；本地行情缓存，不建议提交
└─ reports/                # 自动创建；扫描和回测报告
```

## 安装

建议 Python 3.11/3.12。

```powershell
python -m pip install -r requirements.txt
```

## 使用

### 1. 先检查环境和免费接口

```powershell
python diagnose.py
```

### 2. 扫描市场

先用 50 只测试：

```powershell
python main.py scan --limit 50
```

确认正常后扫描全市场：

```powershell
python main.py scan
```

结果输出到 `reports/scan_latest.csv`。

### 3. 回测

```powershell
python main.py backtest --limit 100
```

不传 `--limit` 可按股票池全部运行，但首次建议先小范围验证接口与策略。

### 4. 网页端

```powershell
python webapp.py
```

浏览器打开：`http://127.0.0.1:5000`

### 5. 生成东方财富公式参考稿

```powershell
python main.py formula
```

会生成 `reports/eastmoney_formula.txt`。

## 当前第一版策略

综合评分由以下维度组成：

- 趋势：MA5 / MA10 / MA20 / MA60；
- 动量：MACD、RSI、KDJ；
- 量能：5日均量比；
- 强弱：5日、20日涨幅与20日高点位置；
- 风险：ATR 波动率、过热状态；
- 出场：ATR 风险线、目标线、均线/动量转弱、最大观察窗口。

这些参数集中在 `config.py`，后续由回测结果调参，不把某个持股天数写死成选股条件。

> 说明：该项目是研究/辅助决策工具，不保证收益。历史回测不代表未来表现。
