from __future__ import annotations

import platform
import sys


def main() -> None:
    print("=" * 60)
    print("A股短期波段量化选股器 - 环境诊断")
    print("=" * 60)
    print("Python:", sys.version.split()[0])
    print("系统:", platform.platform())

    modules = ["pandas", "numpy", "akshare", "flask", "openpyxl", "requests"]
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

    print("\n正在测试免费股票列表接口...")
    try:
        from data import FreeAStockData
        provider = FreeAStockData()
        stocks = provider.stock_list()
        print(f"[OK] 股票列表获取成功，共 {len(stocks)} 只")
        if not stocks.empty:
            sample = stocks.iloc[0]
            print(f"正在测试日线接口: {sample['code']} {sample['name']}")
            hist = provider.history(str(sample["code"]), refresh=True)
            print(f"[OK] 日线获取成功，共 {len(hist)} 根K线，最新日期 {hist.iloc[-1]['date']}")
    except Exception as exc:
        print(f"[FAIL] 免费数据接口测试失败: {exc}")
        print("这通常是网络、接口临时限制或 AKShare 接口变化造成的；代码会保留主源/备用源结构。")


if __name__ == "__main__":
    main()
