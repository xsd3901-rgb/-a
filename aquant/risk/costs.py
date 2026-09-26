from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TradingCostBreakdown:
    commission: float
    transfer_fee: float
    stamp_duty: float
    total: float


@dataclass(frozen=True)
class AShareCostModel:
    """沪深 A 股现货交易成本模型。

    commission_bps 为券商佣金假设，默认万2.5；实际账户佣金可在配置中调整。
    transfer fee 按中国结算历史调整日期处理。
    印花税只在卖出端收取，2023-08-28 起减半。
    slippage_bps 不作为现金手续费，而用于成交价滑点。
    """

    commission_bps: float = 2.5
    min_commission_cny: float = 5.0
    slippage_bps: float = 3.0

    def stamp_duty_rate(self, trade_date: str | pd.Timestamp) -> float:
        date = pd.Timestamp(trade_date).normalize()
        # 2008-09-19 起 A 股印花税改为出让方单边征收；
        # 本项目正式研究区间远晚于该日期。
        return 0.0005 if date >= pd.Timestamp("2023-08-28") else 0.0010

    def transfer_fee_rate(self, trade_date: str | pd.Timestamp) -> float:
        date = pd.Timestamp(trade_date).normalize()
        # 2022-04-29 起沪深 A 股统一按成交金额 0.01‰ 双向收取；
        # 此前沪深 A 股为 0.02‰ 双向收取。
        return 0.00001 if date >= pd.Timestamp("2022-04-29") else 0.00002

    def commission_rate(self) -> float:
        return max(0.0, float(self.commission_bps)) / 10_000.0

    def slippage_rate(self) -> float:
        return max(0.0, float(self.slippage_bps)) / 10_000.0

    def buy_fill_price(self, raw_price: float) -> float:
        return float(raw_price) * (1.0 + self.slippage_rate())

    def sell_fill_price(self, raw_price: float) -> float:
        return float(raw_price) * (1.0 - self.slippage_rate())

    def cash_cost(
        self,
        notional: float,
        trade_date: str | pd.Timestamp,
        *,
        side: str,
        apply_min_commission: bool = True,
    ) -> TradingCostBreakdown:
        notional = max(0.0, float(notional))
        commission = notional * self.commission_rate()
        if apply_min_commission and notional > 0:
            commission = max(float(self.min_commission_cny), commission)

        transfer = notional * self.transfer_fee_rate(trade_date)
        stamp = (
            notional * self.stamp_duty_rate(trade_date)
            if str(side).lower() == "sell"
            else 0.0
        )
        total = commission + transfer + stamp
        return TradingCostBreakdown(
            commission=commission,
            transfer_fee=transfer,
            stamp_duty=stamp,
            total=total,
        )

    def estimated_round_trip_pct(
        self,
        buy_date: str | pd.Timestamp,
        sell_date: str | pd.Timestamp,
    ) -> float:
        """无持仓金额时的比例化成本估计。

        不计最低 5 元佣金，因为最低佣金依赖实际成交金额；组合模拟会按现金金额计算。
        """
        buy_rate = self.commission_rate() + self.transfer_fee_rate(buy_date)
        sell_rate = (
            self.commission_rate()
            + self.transfer_fee_rate(sell_date)
            + self.stamp_duty_rate(sell_date)
        )
        slippage = self.slippage_rate() * 2.0
        return (buy_rate + sell_rate + slippage) * 100.0


DEFAULT_COST_MODEL = AShareCostModel()
