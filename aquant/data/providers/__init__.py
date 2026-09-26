"""A-Quant 数据源适配器。"""

from aquant.data.providers.baostock_provider import BaoStockProvider
from aquant.data.providers.eastmoney_akshare import EastMoneyAKShareProvider

__all__ = ["BaoStockProvider", "EastMoneyAKShareProvider"]
