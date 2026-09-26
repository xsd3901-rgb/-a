from __future__ import annotations

import gc
import math
import shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from aquant.data.context_service import MarketContextService
from aquant.data.service import MarketDataService
from aquant.features.pipeline import FeaturePipeline
from aquant.runtime.resources import current_profile
from config import SETTINGS, ensure_directories


FEATURE_SPECS: dict[str, str] = {
    "close_ma20_gap_pct": "continuous",
    "ma20_slope_5_pct": "continuous",
    "ma60_slope_10_pct": "continuous",
    "ret_5d": "continuous",
    "ret_20d": "continuous",
    "momentum_5_20": "continuous",
    "breakout_20_pct": "continuous",
    "range_position_20": "continuous",
    "vol_ratio20": "continuous",
    "amount_ratio5_20": "continuous",
    "atr_pct": "continuous",
    "volatility_20": "continuous",
    "drawdown_20": "continuous",
    "rs_20d": "continuous",
    "trend_up": "boolean",
    "short_ma_bull": "boolean",
    "near_breakout": "boolean",
    "healthy_volume": "boolean",
    "overheated": "boolean",
}

HORIZONS = (5, 10, 20)


def add_forward_returns(frame: pd.DataFrame) -> pd.DataFrame:
    """仅为研究标签生成未来收益，不参与特征计算。"""
    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    for horizon in HORIZONS:
        out[f"fwd_ret_{horizon}d"] = (
            close.shift(-horizon) / close - 1.0
        ) * 100.0
    return out


def _pearson_rank_corr(x: pd.Series, y: pd.Series) -> float:
    valid = pd.concat([x, y], axis=1).dropna()
    if len(valid) < 3:
        return math.nan
    xr = valid.iloc[:, 0].rank(method="average")
    yr = valid.iloc[:, 1].rank(method="average")
    value = xr.corr(yr)
    return float(value) if pd.notna(value) else math.nan


def _welch_t(a: pd.Series, b: pd.Series) -> float:
    a = pd.to_numeric(a, errors="coerce").dropna()
    b = pd.to_numeric(b, errors="coerce").dropna()
    if len(a) < 2 or len(b) < 2:
        return math.nan
    va = float(a.var(ddof=1))
    vb = float(b.var(ddof=1))
    se = math.sqrt(va / len(a) + vb / len(b))
    if se <= 0:
        return math.nan
    return float((a.mean() - b.mean()) / se)


def _direction_text(value: float) -> str:
    if not math.isfinite(value) or abs(value) < 1e-12:
        return "中性"
    return "高值更优" if value > 0 else "低值更优"


def _stability_label(
    train_effect: float,
    valid_effect: float,
    train_n: int,
    valid_n: int,
) -> str:
    if train_n < 200 or valid_n < 100:
        return "样本不足"
    if not math.isfinite(train_effect) or not math.isfinite(valid_effect):
        return "样本不足"
    if abs(train_effect) < 1e-12 or abs(valid_effect) < 1e-12:
        return "效应很弱"
    if np.sign(train_effect) == np.sign(valid_effect):
        return "方向一致"
    return "方向反转"


def evaluate_feature_samples(
    samples: pd.DataFrame,
    *,
    train_ratio: float = 0.70,
    split_date_override: str | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """对已生成的特征样本做时间切分验证，不自动修改策略权重。"""
    if samples is None or samples.empty:
        return pd.DataFrame(), None

    frame = samples.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"]).sort_values("date")
    dates = pd.Series(frame["date"].drop_duplicates().sort_values().to_list())
    if len(dates) < 10:
        return pd.DataFrame(), None

    if split_date_override is not None:
        split_date = pd.Timestamp(split_date_override).normalize()
    else:
        split_pos = min(
            len(dates) - 2,
            max(1, int(len(dates) * float(train_ratio)) - 1),
        )
        split_date = pd.Timestamp(dates.iloc[split_pos]).normalize()

    rows: list[dict] = []
    for feature, kind in FEATURE_SPECS.items():
        if feature not in frame.columns:
            continue

        for horizon in HORIZONS:
            target = f"fwd_ret_{horizon}d"
            if target not in frame.columns:
                continue

            pair = frame[["date", feature, target]].copy()
            pair[target] = pd.to_numeric(pair[target], errors="coerce")
            pair = pair.dropna(subset=[feature, target])
            train = pair[pair["date"] <= split_date].copy()
            valid = pair[pair["date"] > split_date].copy()

            if kind == "continuous":
                train[feature] = pd.to_numeric(train[feature], errors="coerce")
                valid[feature] = pd.to_numeric(valid[feature], errors="coerce")
                train = train.dropna(subset=[feature])
                valid = valid.dropna(subset=[feature])

                train_ic = _pearson_rank_corr(train[feature], train[target])
                valid_ic = _pearson_rank_corr(valid[feature], valid[target])

                if train.empty:
                    q30 = q70 = math.nan
                    train_effect = valid_effect = math.nan
                    train_t = valid_t = math.nan
                else:
                    q30 = float(train[feature].quantile(0.30))
                    q70 = float(train[feature].quantile(0.70))

                    train_low = train.loc[train[feature] <= q30, target]
                    train_high = train.loc[train[feature] >= q70, target]
                    valid_low = valid.loc[valid[feature] <= q30, target]
                    valid_high = valid.loc[valid[feature] >= q70, target]

                    train_effect = (
                        float(train_high.mean() - train_low.mean())
                        if len(train_high) and len(train_low)
                        else math.nan
                    )
                    valid_effect = (
                        float(valid_high.mean() - valid_low.mean())
                        if len(valid_high) and len(valid_low)
                        else math.nan
                    )
                    train_t = _welch_t(train_high, train_low)
                    valid_t = _welch_t(valid_high, valid_low)

                stability = _stability_label(
                    train_effect,
                    valid_effect,
                    len(train),
                    len(valid),
                )
                rows.append(
                    {
                        "特征": feature,
                        "类型": kind,
                        "预测窗口": f"{horizon}日",
                        "切分日": split_date.strftime("%Y-%m-%d"),
                        "训练样本": len(train),
                        "验证样本": len(valid),
                        "训练SpearmanIC": round(train_ic, 4)
                        if math.isfinite(train_ic)
                        else np.nan,
                        "验证SpearmanIC": round(valid_ic, 4)
                        if math.isfinite(valid_ic)
                        else np.nan,
                        "训练高低组收益差%": round(train_effect, 4)
                        if math.isfinite(train_effect)
                        else np.nan,
                        "验证高低组收益差%": round(valid_effect, 4)
                        if math.isfinite(valid_effect)
                        else np.nan,
                        "验证t值": round(valid_t, 3)
                        if math.isfinite(valid_t)
                        else np.nan,
                        "训练方向": _direction_text(train_effect),
                        "稳定性": stability,
                        "训练30%分位": round(q30, 6)
                        if math.isfinite(q30)
                        else np.nan,
                        "训练70%分位": round(q70, 6)
                        if math.isfinite(q70)
                        else np.nan,
                    }
                )
            else:
                train_bool = train[feature].astype(bool)
                valid_bool = valid[feature].astype(bool)

                train_true = train.loc[train_bool, target]
                train_false = train.loc[~train_bool, target]
                valid_true = valid.loc[valid_bool, target]
                valid_false = valid.loc[~valid_bool, target]

                train_effect = (
                    float(train_true.mean() - train_false.mean())
                    if len(train_true) and len(train_false)
                    else math.nan
                )
                valid_effect = (
                    float(valid_true.mean() - valid_false.mean())
                    if len(valid_true) and len(valid_false)
                    else math.nan
                )
                valid_t = _welch_t(valid_true, valid_false)
                stability = _stability_label(
                    train_effect,
                    valid_effect,
                    len(train),
                    len(valid),
                )
                rows.append(
                    {
                        "特征": feature,
                        "类型": kind,
                        "预测窗口": f"{horizon}日",
                        "切分日": split_date.strftime("%Y-%m-%d"),
                        "训练样本": len(train),
                        "验证样本": len(valid),
                        "训练SpearmanIC": np.nan,
                        "验证SpearmanIC": np.nan,
                        "训练高低组收益差%": round(train_effect, 4)
                        if math.isfinite(train_effect)
                        else np.nan,
                        "验证高低组收益差%": round(valid_effect, 4)
                        if math.isfinite(valid_effect)
                        else np.nan,
                        "验证t值": round(valid_t, 3)
                        if math.isfinite(valid_t)
                        else np.nan,
                        "训练方向": "True更优"
                        if math.isfinite(train_effect) and train_effect > 0
                        else "False更优",
                        "稳定性": stability,
                        "训练30%分位": np.nan,
                        "训练70%分位": np.nan,
                    }
                )

    result = pd.DataFrame(rows)
    if result.empty:
        return result, split_date

    order = {"方向一致": 0, "效应很弱": 1, "方向反转": 2, "样本不足": 3}
    result["_order"] = result["稳定性"].map(order).fillna(9)
    result["_abs_t"] = pd.to_numeric(
        result["验证t值"], errors="coerce"
    ).abs().fillna(0)
    result = (
        result.sort_values(
            ["_order", "_abs_t", "预测窗口", "特征"],
            ascending=[True, False, True, True],
        )
        .drop(columns=["_order", "_abs_t"])
        .reset_index(drop=True)
    )
    return result, split_date


def _research_universe(
    provider: MarketDataService,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    refresh: bool,
) -> pd.DataFrame:
    try:
        stocks = provider.historical_securities(refresh=refresh).copy()
        listing = (
            stocks["listing_date"]
            if "listing_date" in stocks.columns
            else pd.Series(pd.NaT, index=stocks.index)
        )
        delisting = (
            stocks["delisting_date"]
            if "delisting_date" in stocks.columns
            else pd.Series(pd.NaT, index=stocks.index)
        )
        stocks["listing_date"] = pd.to_datetime(
            listing, errors="coerce"
        ).dt.normalize()
        stocks["delisting_date"] = pd.to_datetime(
            delisting, errors="coerce"
        ).dt.normalize()
        listed = stocks["listing_date"].isna() | (
            stocks["listing_date"] <= end_date
        )
        alive = stocks["delisting_date"].isna() | (
            stocks["delisting_date"] >= start_date
        )
        return (
            stocks[listed & alive]
            .drop_duplicates("code")
            .reset_index(drop=True)
        )
    except Exception:
        stocks = provider.stock_list().copy()
        stocks["listing_date"] = pd.NaT
        stocks["delisting_date"] = pd.NaT
        return stocks


def _sample_columns() -> list[str]:
    return [
        "code",
        "name",
        "date",
        *FEATURE_SPECS.keys(),
        *(f"fwd_ret_{h}d" for h in HORIZONS),
    ]


def _flush_chunk(
    buffer: list[pd.DataFrame],
    work_dir: Path,
    chunk_no: int,
) -> int:
    if not buffer:
        return 0
    chunk = pd.concat(buffer, ignore_index=True, sort=False)
    keep = [c for c in _sample_columns() if c in chunk.columns]
    chunk = chunk[keep]
    path = work_dir / f"chunk_{chunk_no:05d}.parquet"
    chunk.to_parquet(path, index=False)
    rows = len(chunk)
    buffer.clear()
    return rows


def _evaluate_feature_store(
    work_dir: Path,
    *,
    train_ratio: float,
) -> tuple[pd.DataFrame, pd.Timestamp | None, int]:
    """按特征逐列读取 Parquet，避免全市场研究样本一次性进入内存。"""
    files = list(work_dir.glob("chunk_*.parquet"))
    if not files:
        return pd.DataFrame(), None, 0

    glob_path = str(work_dir / "chunk_*.parquet").replace("'", "''")
    con = duckdb.connect()
    try:
        dates = con.execute(
            f"""
            SELECT DISTINCT date
            FROM read_parquet('{glob_path}', union_by_name=true)
            WHERE date IS NOT NULL
            ORDER BY date
            """
        ).df()
        sample_count = int(
            con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{glob_path}', union_by_name=true)"
            ).fetchone()[0]
        )

        if len(dates) < 10:
            return pd.DataFrame(), None, sample_count

        date_series = pd.to_datetime(
            dates["date"], errors="coerce"
        ).dropna().sort_values().reset_index(drop=True)
        split_pos = min(
            len(date_series) - 2,
            max(1, int(len(date_series) * float(train_ratio)) - 1),
        )
        split_date = pd.Timestamp(date_series.iloc[split_pos]).normalize()

        rows: list[pd.DataFrame] = []
        for feature in FEATURE_SPECS:
            for horizon in HORIZONS:
                target = f"fwd_ret_{horizon}d"
                try:
                    pair = con.execute(
                        f"""
                        SELECT date, "{feature}", "{target}"
                        FROM read_parquet('{glob_path}', union_by_name=true)
                        WHERE "{feature}" IS NOT NULL
                          AND "{target}" IS NOT NULL
                        """
                    ).df()
                except Exception:
                    continue

                if pair.empty:
                    continue

                one, _ = evaluate_feature_samples(
                    pair,
                    train_ratio=train_ratio,
                    split_date_override=split_date,
                )
                if not one.empty:
                    rows.append(one)

        if not rows:
            return pd.DataFrame(), split_date, sample_count

        result = pd.concat(rows, ignore_index=True, sort=False)
        result = result.drop_duplicates(
            ["特征", "预测窗口"], keep="last"
        )
        order = {"方向一致": 0, "效应很弱": 1, "方向反转": 2, "样本不足": 3}
        result["_order"] = result["稳定性"].map(order).fillna(9)
        result["_abs_t"] = pd.to_numeric(
            result["验证t值"], errors="coerce"
        ).abs().fillna(0)
        result = (
            result.sort_values(
                ["_order", "_abs_t", "预测窗口", "特征"],
                ascending=[True, False, True, True],
            )
            .drop(columns=["_order", "_abs_t"])
            .reset_index(drop=True)
        )
        return result, split_date, sample_count
    finally:
        con.close()


def run_feature_validation(
    limit: int | None = 200,
    refresh: bool = False,
) -> pd.DataFrame:
    """验证特征在训练/验证时间段是否方向一致，但不自动修改正式评分。"""
    ensure_directories()
    provider = MarketDataService()
    context = MarketContextService()
    runtime = current_profile()

    market_end = provider.latest_trade_date()
    study_start = market_end - pd.Timedelta(
        days=SETTINGS.feature_validation_calendar_days
    )
    fetch_start = study_start - pd.Timedelta(
        days=SETTINGS.feature_validation_warmup_calendar_days
    )

    stocks = _research_universe(
        provider,
        study_start,
        market_end,
        refresh=refresh,
    )
    if limit and limit > 0:
        stocks = stocks.head(limit)

    benchmark = context.index_daily(
        "sh000300",
        fetch_start.strftime("%Y-%m-%d"),
        market_end.strftime("%Y-%m-%d"),
    )

    work_dir = SETTINGS.data_store_dir / "research" / "feature_validation"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    pipeline = FeaturePipeline()
    buffer: list[pd.DataFrame] = []
    total_rows = 0
    valid_stocks = 0
    errors: list[dict] = []
    chunk_no = 0
    flush_every = max(1, runtime.batch_size)

    print(
        f"特征验证股票池 {len(stocks)} 只 | "
        f"研究区间 {study_start:%Y-%m-%d} ~ {market_end:%Y-%m-%d}"
    )

    for pos, row in stocks.iterrows():
        code, name = str(row["code"]), str(row["name"])
        listing_date = row.get("listing_date", pd.NaT)
        delisting_date = row.get("delisting_date", pd.NaT)

        stock_start = fetch_start
        if pd.notna(listing_date):
            stock_start = max(stock_start, pd.Timestamp(listing_date).normalize())

        stock_end = market_end
        if pd.notna(delisting_date):
            stock_end = min(stock_end, pd.Timestamp(delisting_date).normalize())

        if stock_start >= stock_end:
            continue

        try:
            hist = provider.history_range(
                code,
                stock_start.strftime("%Y-%m-%d"),
                stock_end.strftime("%Y-%m-%d"),
                refresh=refresh,
                prefer_point_in_time=True,
            )
            if len(hist) < SETTINGS.min_bars + max(HORIZONS):
                continue

            feat = pipeline.transform(hist, benchmark_bars=benchmark)
            feat = add_forward_returns(feat)
            feat["date"] = pd.to_datetime(
                feat["date"], errors="coerce"
            ).dt.normalize()
            feat = feat[
                (feat["date"] >= study_start)
                & (feat["date"] <= market_end)
            ].copy()

            if "trade_status" in feat.columns:
                feat = feat[
                    pd.to_numeric(
                        feat["trade_status"], errors="coerce"
                    ).fillna(1).eq(1)
                ]
            if SETTINGS.exclude_st and "is_st" in feat.columns:
                feat = feat[
                    ~pd.to_numeric(
                        feat["is_st"], errors="coerce"
                    ).fillna(0).eq(1)
                ]

            if feat.empty:
                continue

            feat["code"] = code
            feat["name"] = name
            buffer.append(feat)
            valid_stocks += 1

        except Exception as exc:
            errors.append({"代码": code, "名称": name, "错误": str(exc)[:300]})

        finally:
            if (pos + 1) % flush_every == 0:
                chunk_no += 1
                total_rows += _flush_chunk(buffer, work_dir, chunk_no)
                print(
                    f"特征验证进度 {pos + 1}/{len(stocks)} | "
                    f"有效股票 {valid_stocks} | 样本 {total_rows}"
                )
                gc.collect()

    if buffer:
        chunk_no += 1
        total_rows += _flush_chunk(buffer, work_dir, chunk_no)

    result, split_date, sample_count = _evaluate_feature_store(
        work_dir,
        train_ratio=SETTINGS.feature_validation_train_ratio,
    )
    result.to_csv(
        SETTINGS.report_dir / "feature_validation.csv",
        index=False,
        encoding="utf-8-sig",
    )

    meta = pd.DataFrame(
        [
            {
                "研究开始": study_start.strftime("%Y-%m-%d"),
                "研究结束": market_end.strftime("%Y-%m-%d"),
                "训练验证切分日": split_date.strftime("%Y-%m-%d")
                if split_date is not None
                else "",
                "股票池数量": len(stocks),
                "有效股票数量": valid_stocks,
                "样本行数": sample_count,
                "错误数量": len(errors),
                "说明": "仅验证特征稳定性，不自动修改正式选股评分权重",
            }
        ]
    )
    meta.to_csv(
        SETTINGS.report_dir / "feature_validation_meta.csv",
        index=False,
        encoding="utf-8-sig",
    )
    if errors:
        pd.DataFrame(errors).to_csv(
            SETTINGS.report_dir / "feature_validation_errors.csv",
            index=False,
            encoding="utf-8-sig",
        )

    print(
        f"特征验证完成：有效股票 {valid_stocks}，样本 {sample_count}，"
        f"结果 {len(result)} 行。"
    )
    return result
