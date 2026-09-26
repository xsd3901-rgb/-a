from __future__ import annotations

import platform
import sys


def main() -> None:
    print("=" * 60)
    print("A股短期波段量化选股器 - 环境诊断")
    print("=" * 60)
    print("Python:", sys.version.split()[0])
    print("系统:", platform.platform())

    modules = [
        "pandas",
        "numpy",
        "akshare",
        "baostock",
        "duckdb",
        "pyarrow",
        "psutil",
        "flask",
        "openpyxl",
        "requests",
    ]
    failed = []
    for name in modules:
        try:
            mod = __import__(name)
            version = getattr(mod, "__version__", "unknown")
            print(f"[OK] {name}: {version}")
        except Exception as exc:
            failed.append(name)
            print(f"[FAIL] {name}: {exc}")

    if failed:
        print("\n缺少依赖，请运行: python -m pip install -r requirements.txt")
        raise SystemExit(1)

    print("\n正在检查内存自适应资源档位...")
    try:
        from aquant.runtime.resources import current_memory_gb, current_profile

        total_gb, available_gb = current_memory_gb()
        runtime = current_profile()
        print(
            f"[OK] 总内存 {total_gb:.1f} GB，可用 {available_gb:.1f} GB，"
            f"档位 {runtime.name}，批次 {runtime.batch_size}"
        )
    except Exception as exc:
        print(f"[FAIL] 内存自适应检查失败: {exc}")
        raise SystemExit(1) from exc

    print("\n正在测试新版统一数据层...")
    try:
        from aquant.data.service import MarketDataService

        provider = MarketDataService()

        stocks = provider.stock_list()
        if stocks.empty:
            raise RuntimeError("股票基础库为空")
        print(f"[OK] 股票基础库可用，共 {len(stocks)} 只")

        calendar = provider.trade_calendar()
        if calendar.empty:
            raise RuntimeError("交易日历为空")
        latest_trade = provider.latest_trade_date()
        print(f"[OK] 交易日历可用，最近交易日 {latest_trade:%Y-%m-%d}")

        sample = stocks.iloc[0]
        print(f"正在测试日线、本地库和备用源链路: {sample['code']} {sample['name']}")
        hist = provider.history(str(sample["code"]), refresh=True)
        if hist.empty:
            raise RuntimeError("日线返回为空")
        print(f"[OK] 日线获取成功，共 {len(hist)} 根K线，最新日期 {hist.iloc[-1]['date']}")

        provider.refresh_catalog()
        print("[OK] 股票基础库/交易日历/日线已进入 Parquet，并刷新 DuckDB 目录视图")

        from aquant.data.context_service import MarketContextService

        context = MarketContextService()
        industry = context.industry_map()
        if industry.empty:
            raise RuntimeError("行业映射为空")
        print(f"[OK] 行业映射可用，共 {len(industry)} 条")

        index_start = (latest_trade - __import__("pandas").Timedelta(days=45)).strftime("%Y-%m-%d")
        index_end = latest_trade.strftime("%Y-%m-%d")
        index_df = context.index_daily("sh000001", index_start, index_end)
        if index_df.empty:
            raise RuntimeError("上证指数日线为空")
        print(f"[OK] 上证指数日线可用，共 {len(index_df)} 根，最新日期 {index_df.iloc[-1]['trade_date']}")

        context.store.refresh_catalog()
        print("[OK] 指数/行业数据已进入 Parquet，并刷新 DuckDB 目录视图")
        print("[OK] 数据层端到端诊断通过")
    except Exception as exc:
        print(f"[FAIL] 新版数据层测试失败: {exc}")
        print("主源失败时会尝试 BaoStock；若两者都失败，请检查网络或免费接口临时限制。")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
