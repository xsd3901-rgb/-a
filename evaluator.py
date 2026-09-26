from __future__ import annotations

import math

import numpy as np
import pandas as pd


def evaluate_trades(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty or "净收益%" not in trades.columns:
        return {
            "交易次数": 0,
            "胜率%": 0.0,
            "平均收益%": 0.0,
            "中位收益%": 0.0,
            "盈亏比": 0.0,
            "最大回撤%": 0.0,
            "平均持有交易日": 0.0,
            "策略健康度": "样本不足",
            "说明": "没有足够交易样本，不能判断策略稳定性。",
        }

    r = pd.to_numeric(trades["净收益%"], errors="coerce").dropna()
    if r.empty:
        return evaluate_trades(pd.DataFrame())

    wins = r[r > 0]
    losses = r[r < 0]
    win_rate = (r > 0).mean() * 100
    avg_return = r.mean()
    median_return = r.median()

    avg_win = wins.mean() if not wins.empty else 0.0
    avg_loss = abs(losses.mean()) if not losses.empty else math.inf
    payoff = (avg_win / avg_loss) if avg_loss not in (0, math.inf) else 0.0

    equity = (1 + r / 100).cumprod()
    peak = equity.cummax()
    drawdown = (equity / peak - 1) * 100
    max_drawdown = abs(float(drawdown.min())) if not drawdown.empty else 0.0

    hold = pd.to_numeric(trades.get("持有交易日", pd.Series(dtype=float)), errors="coerce")
    avg_hold = float(hold.mean()) if not hold.empty else 0.0

    n = len(r)
    if n < 30:
        health = "样本不足"
        note = "交易样本少于30笔，先扩大回测范围，不建议据此定参数。"
    elif avg_return <= 0 or win_rate < 40:
        health = "需要优化"
        note = "当前样本的期望收益或胜率偏弱，应检查阈值、退出规则和市场阶段适应性。"
    elif max_drawdown > 25:
        health = "风险偏高"
        note = "策略有正向表现，但回撤偏大，应优先优化风险控制。"
    else:
        health = "可继续验证"
        note = "当前历史样本通过基础健康检查，仍需扩大时间区间并做样本外验证。"

    return {
        "交易次数": int(n),
        "胜率%": round(float(win_rate), 2),
        "平均收益%": round(float(avg_return), 3),
        "中位收益%": round(float(median_return), 3),
        "盈亏比": round(float(payoff), 3),
        "最大回撤%": round(float(max_drawdown), 2),
        "平均持有交易日": round(float(avg_hold), 2),
        "策略健康度": health,
        "说明": note,
    }


def metrics_frame(metrics: dict) -> pd.DataFrame:
    return pd.DataFrame([metrics])
