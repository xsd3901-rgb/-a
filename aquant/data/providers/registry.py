from __future__ import annotations

from aquant.data.providers.baostock_provider import BaoStockProvider
from aquant.data.providers.eastmoney_akshare import EastMoneyAKShareProvider


class ProviderRegistry:
    """集中注册正式日线 Provider，避免业务层直接拼装适配器。"""

    def __init__(self) -> None:
        self._providers = {
            "eastmoney": EastMoneyAKShareProvider(),
            "baostock": BaoStockProvider(),
        }

    def get(self, name: str):
        try:
            return self._providers[str(name)]
        except KeyError as exc:
            raise KeyError(f"未注册的数据源: {name}") from exc

    def ordered(self, names) -> list:
        return [
            self.get(name)
            for name in names
            if str(name) in self._providers
        ]

    def names(self) -> list[str]:
        return list(self._providers)
