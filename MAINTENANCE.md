# A-Quant 维护地图

这份文件只回答一个问题：**以后某个功能出问题，应该改哪里。**

A-Quant 保持既定量化架构不变；这里描述的是代码职责边界，避免页面、数据下载、策略、回测再次堆进同一个文件。

## 1. 网页终端

```text
webapp.py
└─ 只负责启动 Flask

aquant/web/
├─ app.py                 Flask 应用工厂
├─ routes.py              HTTP 页面/API 路由
├─ jobs.py                后台任务状态、一次一个重任务
├─ reports.py             reports 文件列表
├─ serializers.py         DataFrame/JSON 安全转换
├─ action_context.py      网页任务统一参数
├─ actions/
│  ├─ daily.py            扫描 / 单股 / 回测
│  ├─ data.py             建库 / 审计 / 修复 / 基础资料
│  ├─ research.py         特征 / 消融 / WF / V1-V2 / 优化
│  └─ system.py           首次完整准备 / 就绪 / 东财公式
├─ templates/
│  └─ index.html          页面结构
└─ static/
   ├─ app.css             颜色、布局、响应式
   └─ app.js              按钮、任务轮询、表格/结果展示
```

常见修改：

- 改颜色/大小/布局：`aquant/web/static/app.css`
- 改按钮文字/页面分区：`aquant/web/templates/index.html`
- 改按钮提交参数/结果展示：`aquant/web/static/app.js`
- 新增网页 API：`aquant/web/routes.py`
- 新增数据任务：`aquant/web/actions/data.py`
- 新增研究任务：`aquant/web/actions/research.py`

**不要把策略计算重新写回 web 层。**

## 2. 数据源与路由

```text
aquant/data/providers/
├─ base.py                 Provider 字段契约
├─ registry.py             正式日线 Provider 注册表
├─ eastmoney_akshare.py    EastMoney / AKShare 适配
├─ baostock_provider.py    BaoStock 日线适配
└─ baostock_session.py     BaoStock 长连接/串行会话

aquant/data/routing/
├─ policy.py               各类数据的源顺序和超时
└─ router.py               健康感知路由与熔断

aquant/data/
├─ service.py              行情统一服务
├─ reference_service.py    股票基础表 / 交易日历
├─ universe_service.py     历史上市退市生命周期
├─ source_health.py        连续失败、冷却、健康评分
├─ source_quality.py       每次行情抓取质量事件
├─ overview.py             网页“数据中心概览”
├─ storage.py              Parquet + DuckDB 行情存储
└─ seed_reference.py       随项目内置的启动种子

aquant/resources/
├─ security_master_seed.csv
├─ trade_calendar_seed.csv
├─ seed_manifest.json
└─ NOTICE.md
```

常见修改：

- EastMoney 接口字段变了：`providers/eastmoney_akshare.py`
- BaoStock 接口变了：`providers/baostock_provider.py`
- 想加新日线源：先注册到 `providers/registry.py`
- 改“谁优先、等几秒”：只改 `routing/policy.py`
- 改熔断规则/健康评分：`source_health.py`
- 改股票列表/交易日历降级：`reference_service.py`
- 改历史股票池：`universe_service.py`
- 改本地 Parquet/DuckDB：`storage.py`

原则：

```text
Provider 只负责取数和字段标准化
        ↓
Router 只负责源选择/熔断
        ↓
Service 负责编排与本地优先
        ↓
Store 只负责落盘/读取
        ↓
Audit 决定数据能否进入正式研究
```

## 3. 现在的数据获取策略

### 日常运行

```text
本地 Parquet / 本地基础库
        ↓
缺数据才访问网络
        ↓
健康路由选择可用免费源
        ↓
成功立即落盘
        ↓
下次直接读本地
```

### 首次部署

股票列表和交易日历带有 bootstrap seed，所以公共接口临时不可用时，不再因为前置元数据失败而完全无法开始。

这些 seed **只用于启动**。正式研究仍要求：

- 历史 security_lifecycle 可用；
- 未复权真实行情覆盖达到审计要求；
- 点时字段达到要求；
- 正式 data audit 通过。

### 数据源熔断

健康状态按能力独立，例如：

```text
stock_list:eastmoney
daily:eastmoney
calendar:baostock
lifecycle:baostock
```

所以“东财股票列表接口失败”不会错误禁用“东财历史日线”。

连续失败后进入短暂冷却，避免几千只股票反复等同一个坏接口。冷却结束后自动允许重新探测。

## 4. BaoStock

正式日线下载使用进程内复用会话：

```text
login
  ↓
连续处理多只股票（串行）
  ↓
达到请求批次上限或异常
  ↓
logout / 重连
```

不要再在每只股票外层手工增加 `login/logout`。

## 5. 策略和研究层

仍按原架构：

- `strategy.py`：V1 评分逻辑
- `scanner.py`：全市场扫描编排
- `backtest.py`：正式真实成交回测
- `optimizer.py`：受控参数优化
- `aquant/research/`：特征、消融、Walk-Forward、V1/V2、数据审计
- `aquant/risk/`：独立风险与交易规则

**网页和数据源重构不得改变这些研究口径。**

## 6. 修改后的最低自检

在 PyCharm Terminal 中：

```powershell
python -m compileall -q .
python tests/smoke_data_offline.py
python tests/smoke_source_routing_offline.py
python tests/smoke_webapp_offline.py
```

三项通过后，再做真实网络测试：

```powershell
python diagnose.py
```

最后才进行全市场真实建库/验收。

## 7. 最重要的维护规则

1. 不把网络请求写进策略评分函数。
2. 不把策略计算写进 Flask 路由。
3. 不让 Provider 直接决定策略使用哪个源。
4. 不用“延长 timeout”代替熔断和本地缓存。
5. 不因为单只股票失败重建全市场。
6. 不把 bootstrap seed 当正式历史生命周期。
7. 任何模型升级仍必须经过独立数据审计、Walk-Forward 和真实成交回测。
