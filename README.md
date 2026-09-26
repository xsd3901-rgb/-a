# A股短期波段量化选股器

这是按既定方案搭建的第一版基础工程，目标是：

- 扫描沪深北 A 股市场，按日线数据进行短期波段选股；
- 持有周期由信号、止损、趋势变化和回测结果决定，“约一周到30天”只作为回测观察范围，不是硬性选股条件；
- 优先使用免费数据源，并保留备用数据源切换能力；
- 根据电脑实际可用内存自动调整批次、并发和缓存策略；
- 选股逻辑、数据层、回测、策略评估、参数优化、网页端彼此分离，后续只迭代模块，不随意改架构；
- 参数优化采用受控候选范围 + 训练/验证分段，验证不过就不自动替换当前参数，尽量减少过拟合。

> 东方财富公式/指标转换暂不属于当前开发范围。等策略、回测和终端稳定，并进入真正落地使用阶段时再启用。

## 目录

```text
-a/
├─ main.py                 # 命令行入口
├─ config.py               # 全局默认设置
├─ strategy_profile.json   # 当前活动策略参数
├─ profile.py              # 活动参数读取/安全范围限制
├─ data.py                 # 免费数据源 + 本地缓存
├─ indicators.py           # MA/MACD/KDJ/RSI/ATR/量能等
├─ strategy.py             # 短期波段评分模型
├─ scanner.py              # 全市场扫描
├─ backtest.py             # 动态退出回测
├─ evaluator.py            # 回测统计与策略健康度
├─ optimizer.py            # 训练/验证分段参数优化
├─ webapp.py               # 本地网页端
├─ diagnose.py             # 环境/接口诊断
├─ requirements.txt
├─ data_cache/             # 自动创建；本地行情缓存，不提交
└─ reports/                # 自动创建；扫描、回测、优化报告
```

## 安装

建议 Python 3.11/3.12。

```powershell
python -m pip install -r requirements.txt
```

## 使用顺序

### 1. 检查环境和免费接口

```powershell
python diagnose.py
```

### 2. 小范围扫描测试

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

### 4. 受控参数优化

```powershell
python main.py optimize --limit 100
```

优化器会：

1. 只在限定候选参数中比较，不无限搜索；
2. 把历史数据划分为训练段和验证段；
3. 同时检查平均收益、交易数量、回撤等；
4. 只有验证段通过基础稳健性检查时，才更新 `strategy_profile.json`；
5. 如果没有候选参数通过检查，就保持原参数不变。

查看当前活动参数：

```powershell
python main.py profile
```

### 5. 网页端

```powershell
python webapp.py
```

浏览器打开：`http://127.0.0.1:5000`

网页端可直接做扫描、回测、参数优化和查看当前策略参数。

## 当前第一版策略

综合评分由以下维度组成：

- 趋势：MA5 / MA10 / MA20 / MA60；
- 动量：MACD、RSI、KDJ；
- 量能：5日均量比；
- 强弱：5日、20日涨幅与20日高点位置；
- 风险：ATR 波动率、过热状态；
- 出场：ATR 风险线、目标线、均线/动量转弱、最大观察窗口。

默认参数保存在 `strategy_profile.json`，基础安全边界由 `profile.py` 控制。参数可以由回测优化器自动更新，但不会把固定持股天数写成选股硬条件。

> 说明：该项目是研究/辅助决策工具，不保证收益。历史回测和样本外验证也不代表未来表现。
