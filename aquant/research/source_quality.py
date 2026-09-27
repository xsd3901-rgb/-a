from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from aquant.data.quality import cross_check_bars
from aquant.data.schema import FIELDS
from aquant.data.source_quality import SourceQualityLog
from config import SETTINGS, ensure_directories


def _provider_events(
    data_root: Path,
    *,
    max_events: int | None = 200_000,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    events = SourceQualityLog(data_root).read(max_events=max_events)
    if not events:
        empty = pd.DataFrame(
            columns=[
                "数据源",
                "适配器",
                "复权",
                "尝试次数",
                "成功次数",
                "首选成功",
                "回退成功",
                "空结果",
                "质量失败",
                "异常",
                "成功率%",
                "平均耗时ms",
                "P95耗时ms",
                "成功返回行数",
                "最近成功",
                "最近异常",
            ]
        )
        return pd.DataFrame(), empty

    frame = pd.DataFrame(events)
    for col in (
        "provider",
        "adapter",
        "adjust",
        "outcome",
        "symbol",
        "time",
        "detail",
    ):
        if col not in frame.columns:
            frame[col] = ""
        frame[col] = frame[col].fillna("").astype(str)

    frame["attempt_order"] = pd.to_numeric(
        frame.get("attempt_order", 1),
        errors="coerce",
    ).fillna(1).astype(int)
    frame["rows"] = pd.to_numeric(
        frame.get("rows", 0),
        errors="coerce",
    ).fillna(0)
    frame["elapsed_ms"] = pd.to_numeric(
        frame.get("elapsed_ms", 0),
        errors="coerce",
    ).fillna(0.0)

    rows: list[dict] = []
    for (provider, adapter, adjust), group in frame.groupby(
        ["provider", "adapter", "adjust"],
        dropna=False,
    ):
        success = group["outcome"].eq("success")
        first_success = success & group["attempt_order"].eq(1)
        fallback_success = success & group["attempt_order"].gt(1)
        attempts = int(len(group))
        success_count = int(success.sum())

        success_times = group.loc[success, "time"]
        problem = group[
            group["outcome"].isin(
                ["error", "empty", "quality_fail"]
            )
        ]

        rows.append(
            {
                "数据源": provider or "unknown",
                "适配器": adapter or "unknown",
                "复权": adjust or "unknown",
                "尝试次数": attempts,
                "成功次数": success_count,
                "首选成功": int(first_success.sum()),
                "回退成功": int(fallback_success.sum()),
                "空结果": int(group["outcome"].eq("empty").sum()),
                "质量失败": int(
                    group["outcome"].eq("quality_fail").sum()
                ),
                "异常": int(group["outcome"].eq("error").sum()),
                "成功率%": round(
                    success_count / attempts * 100.0,
                    2,
                )
                if attempts
                else 0.0,
                "平均耗时ms": round(
                    float(group["elapsed_ms"].mean()),
                    2,
                ),
                "P95耗时ms": round(
                    float(group["elapsed_ms"].quantile(0.95)),
                    2,
                ),
                "成功返回行数": int(
                    group.loc[success, "rows"].sum()
                ),
                "最近成功": (
                    str(success_times.max())
                    if not success_times.empty
                    else ""
                ),
                "最近异常": (
                    str(problem["time"].max())
                    if not problem.empty
                    else ""
                ),
            }
        )

    summary = pd.DataFrame(rows)
    if not summary.empty:
        summary = summary.sort_values(
            ["复权", "数据源", "适配器"]
        ).reset_index(drop=True)
    return frame, summary


def _standard_usage(
    data_root: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "需要 duckdb 才能生成数据源质量报告"
        ) from exc

    detail_parts: list[pd.DataFrame] = []
    base = data_root / "standard" / "daily"

    for adjust, layer in (
        ("none", "Raw未复权执行层"),
        ("qfq", "QFQ兼容扫描层"),
    ):
        folder = base / adjust
        if not folder.exists() or not any(folder.glob("*.parquet")):
            continue

        glob_path = (folder / "*.parquet").as_posix()
        con = duckdb.connect()
        try:
            query = """
                SELECT
                    filename,
                    COALESCE(CAST(provider AS VARCHAR), 'unknown')
                        AS provider,
                    COUNT(*) AS rows,
                    MIN(trade_date) AS start_date,
                    MAX(trade_date) AS end_date
                FROM read_parquet(
                    ?,
                    filename=true,
                    union_by_name=true
                )
                GROUP BY filename, provider
                ORDER BY filename, provider
            """
            part = con.execute(query, [glob_path]).df()
        finally:
            con.close()

        if part.empty:
            continue

        part["代码"] = part["filename"].map(
            lambda value: Path(str(value)).stem.zfill(6)
        )
        part["层"] = layer
        part = part.rename(
            columns={
                "provider": "数据源",
                "rows": "记录数",
                "start_date": "起始日",
                "end_date": "最新日",
            }
        )
        detail_parts.append(
            part[
                [
                    "代码",
                    "层",
                    "数据源",
                    "记录数",
                    "起始日",
                    "最新日",
                ]
            ]
        )

    if not detail_parts:
        return pd.DataFrame(), pd.DataFrame()

    detail = pd.concat(
        detail_parts,
        ignore_index=True,
        sort=False,
    )
    detail["记录数"] = pd.to_numeric(
        detail["记录数"],
        errors="coerce",
    ).fillna(0).astype(int)
    detail["起始日"] = pd.to_datetime(
        detail["起始日"],
        errors="coerce",
    ).dt.strftime("%Y-%m-%d")
    detail["最新日"] = pd.to_datetime(
        detail["最新日"],
        errors="coerce",
    ).dt.strftime("%Y-%m-%d")

    mix = (
        detail.groupby(["代码", "层"])["数据源"]
        .nunique()
        .rename("来源数")
        .reset_index()
    )
    detail = detail.merge(
        mix,
        on=["代码", "层"],
        how="left",
    )
    detail["混合来源"] = detail["来源数"].gt(1)

    usage = (
        detail.groupby(["层", "数据源"], dropna=False)
        .agg(
            股票数=("代码", "nunique"),
            记录数=("记录数", "sum"),
            最早日期=("起始日", "min"),
            最新日期=("最新日", "max"),
        )
        .reset_index()
    )
    usage["混合来源股票数"] = usage.apply(
        lambda row: int(
            detail[
                detail["层"].eq(row["层"])
                & detail["数据源"].eq(row["数据源"])
                & detail["混合来源"]
            ]["代码"].nunique()
        ),
        axis=1,
    )
    return detail, usage


def _cross_source_check(
    data_root: Path,
    *,
    limit: int | None = None,
) -> pd.DataFrame:
    east_dir = (
        data_root
        / "raw"
        / "eastmoney"
        / "daily"
        / "none"
    )
    bao_dir = (
        data_root
        / "raw"
        / "baostock"
        / "daily"
        / "none"
    )
    if not east_dir.exists() or not bao_dir.exists():
        return pd.DataFrame()

    east = {
        path.stem.zfill(6): path
        for path in east_dir.glob("*.parquet")
    }
    bao = {
        path.stem.zfill(6): path
        for path in bao_dir.glob("*.parquet")
    }
    codes = sorted(set(east) & set(bao))
    if limit and limit > 0:
        codes = codes[: int(limit)]

    rows: list[dict] = []
    columns = [
        FIELDS.symbol,
        FIELDS.trade_date,
        FIELDS.close,
        FIELDS.volume,
    ]

    for code in codes:
        try:
            left = pd.read_parquet(
                east[code],
                columns=columns,
            )
            right = pd.read_parquet(
                bao[code],
                columns=columns,
            )
            compared = cross_check_bars(left, right)
            if compared.empty:
                rows.append(
                    {
                        "代码": code,
                        "重叠交易日": 0,
                        "差异交易日": 0,
                        "差异率%": 0.0,
                        "收盘均值差异%": 0.0,
                        "收盘最大差异%": 0.0,
                        "成交量均值差异%": 0.0,
                        "成交量最大差异%": 0.0,
                        "状态": "无重叠",
                    }
                )
                continue

            mismatch = compared["quality_status"].eq(
                "mismatch"
            )
            rows.append(
                {
                    "代码": code,
                    "重叠交易日": int(len(compared)),
                    "差异交易日": int(mismatch.sum()),
                    "差异率%": round(
                        float(mismatch.mean()) * 100.0,
                        3,
                    ),
                    "收盘均值差异%": round(
                        float(
                            pd.to_numeric(
                                compared["close_diff_pct"],
                                errors="coerce",
                            ).mean()
                        ),
                        4,
                    ),
                    "收盘最大差异%": round(
                        float(
                            pd.to_numeric(
                                compared["close_diff_pct"],
                                errors="coerce",
                            ).max()
                        ),
                        4,
                    ),
                    "成交量均值差异%": round(
                        float(
                            pd.to_numeric(
                                compared["volume_diff_pct"],
                                errors="coerce",
                            ).mean()
                        ),
                        4,
                    ),
                    "成交量最大差异%": round(
                        float(
                            pd.to_numeric(
                                compared["volume_diff_pct"],
                                errors="coerce",
                            ).max()
                        ),
                        4,
                    ),
                    "状态": (
                        "存在差异"
                        if bool(mismatch.any())
                        else "一致"
                    ),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "代码": code,
                    "重叠交易日": 0,
                    "差异交易日": 0,
                    "差异率%": 0.0,
                    "收盘均值差异%": 0.0,
                    "收盘最大差异%": 0.0,
                    "成交量均值差异%": 0.0,
                    "成交量最大差异%": 0.0,
                    "状态": "检查失败",
                    "错误": str(exc)[:300],
                }
            )

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            ["差异率%", "收盘最大差异%"],
            ascending=[False, False],
        ).reset_index(drop=True)
    return result


def run_source_quality(
    *,
    crosscheck_limit: int | None = None,
    include_crosscheck: bool = True,
    max_events: int | None = 200_000,
    data_root: str | Path | None = None,
    report_dir: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """只读本地数据，生成行情源可靠性、实际使用和多源差异报告。"""
    ensure_directories()
    data_root = Path(data_root or SETTINGS.data_store_dir)
    report_dir = Path(report_dir or SETTINGS.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    events, providers = _provider_events(
        data_root,
        max_events=max_events,
    )
    usage_detail, usage = _standard_usage(data_root)
    crosscheck = (
        _cross_source_check(
            data_root,
            limit=crosscheck_limit,
        )
        if include_crosscheck
        else pd.DataFrame()
    )

    success_events = (
        int(events["outcome"].eq("success").sum())
        if not events.empty
        else 0
    )
    fallback_success = (
        int(
            (
                events["outcome"].eq("success")
                & events["attempt_order"].gt(1)
            ).sum()
        )
        if not events.empty
        else 0
    )
    quality_fail = (
        int(events["outcome"].eq("quality_fail").sum())
        if not events.empty
        else 0
    )
    error_events = (
        int(
            events["outcome"].isin(
                ["error", "empty"]
            ).sum()
        )
        if not events.empty
        else 0
    )
    mixed_files = (
        int(
            usage_detail.loc[
                usage_detail["混合来源"],
                ["代码", "层"],
            ].drop_duplicates().shape[0]
        )
        if not usage_detail.empty
        else 0
    )
    compared = int(len(crosscheck))
    mismatch_symbols = (
        int(crosscheck["状态"].eq("存在差异").sum())
        if not crosscheck.empty
        else 0
    )

    if events.empty and usage.empty:
        state = "暂无数据源追踪数据"
    elif quality_fail or error_events or mismatch_symbols:
        state = "已追踪，存在需要查看的异常/回退记录"
    else:
        state = "已追踪，当前未发现记录级异常"

    summary = {
        "状态": state,
        "抓取事件数": int(len(events)),
        "成功抓取": success_events,
        "回退源成功": fallback_success,
        "回退成功占比%": round(
            fallback_success / success_events * 100.0,
            2,
        )
        if success_events
        else 0.0,
        "质量校验失败事件": quality_fail,
        "空结果或异常事件": error_events,
        "当前来源使用组合数": int(len(usage)),
        "混合来源股票层": mixed_files,
        "多源交叉检查股票": compared,
        "多源存在差异股票": mismatch_symbols,
        "事件日志": str(
            data_root / "quality" / "source_events.jsonl"
        ),
        "说明": (
            "数据源追踪只记录实际抓取、回退和本地多源差异；"
            "不会为了统计而额外请求付费或远端行情。"
        ),
    }

    providers.to_csv(
        report_dir / "data_source_quality.csv",
        index=False,
        encoding="utf-8-sig",
    )
    usage.to_csv(
        report_dir / "data_source_usage.csv",
        index=False,
        encoding="utf-8-sig",
    )
    usage_detail.to_csv(
        report_dir / "data_source_usage_detail.csv",
        index=False,
        encoding="utf-8-sig",
    )
    if include_crosscheck:
        crosscheck.to_csv(
            report_dir / "data_source_crosscheck.csv",
            index=False,
            encoding="utf-8-sig",
        )
    (
        SETTINGS.report_dir
        / "data_source_quality_summary.json"
    ).write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    return providers, usage, crosscheck, summary
