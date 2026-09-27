from __future__ import annotations

from aquant.research.portfolio import simulate_portfolio
from aquant.research.stock_detail import stock_detail
from aquant.web.action_context import ActionContext
from aquant.web.serializers import records
from backtest import run_backtest
from evaluator import evaluate_by_market_regime, evaluate_trades
from scanner import scan_market


def scan_action(ctx: ActionContext) -> dict:
    frame = scan_market(
        limit=ctx.limit,
        refresh=ctx.refresh,
        progress=ctx.progress,
    )
    return {
        "summary": {"入选数量": len(frame)},
        "rows": records(frame, 100),
    }


def stock_detail_action(ctx: ActionContext) -> dict:
    code = str(ctx.payload.get("code") or "").strip()
    if not code:
        raise ValueError("请输入股票代码")
    summary, recent = stock_detail(code, refresh=ctx.refresh)
    return {
        "summary": summary,
        "rows": records(recent, 60),
    }


def backtest_action(ctx: ActionContext) -> dict:
    trades = run_backtest(
        limit=ctx.limit,
        refresh=ctx.refresh,
        progress=ctx.progress,
    )
    metrics = evaluate_trades(trades)
    by_regime = evaluate_by_market_regime(trades)
    _, equity, portfolio = simulate_portfolio(trades)
    return {
        "metrics": metrics,
        "portfolio": portfolio,
        "by_regime": records(by_regime, 20),
        "trades": records(trades, 80),
        "equity_tail": records(equity.tail(30), 30),
    }


ACTIONS = {
    "scan": scan_action,
    "stock_detail": stock_detail_action,
    "backtest": backtest_action,
}
