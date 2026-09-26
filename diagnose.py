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
        return

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

    print("\n正在测试新版统一数据层...")
    try:
        from aquant.data.service import MarketDataService

        provider = MarketDataService()
        stocks = provider.stock_list()
        print(f"[OK] 股票列表获取成功，共 {len(stocks)} 只")
        if not stocks.empty:
            sample = stocks.iloc[0]
            print(f"正在测试日线、本地库和备用源链路: {sample['code']} {sample['name']}")
            hist = provider.history(str(sample["code"]), refresh=True)
            if hist.empty:
                raise RuntimeError("日线返回为空")
            print(f"[OK] 日线获取成功，共 {len(hist)} 根K线，最新日期 {hist.iloc[-1]['date']}")
            print("[OK] 数据已通过统一字段/质量检查并进入 Parquet/DuckDB 数据层")
    except Exception as exc:
        print(f"[FAIL] 新版数据层测试失败: {exc}")
        print("这通常是网络、免费接口临时限制或接口字段变化造成的；主源失败时会尝试 BaoStock 备用源。")


if __name__ == "__main__":
    main()
