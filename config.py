from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    # 路径：全部锚定项目根目录，避免从其他 PowerShell 目录启动时把数据写到别处。
    project_root: Path = PROJECT_ROOT
    data_store_dir: Path = PROJECT_ROOT / "data_store"
    cache_dir: Path = PROJECT_ROOT / "data_cache"
    report_dir: Path = PROJECT_ROOT / "reports"

    # 数据
    history_days: int = 320
    bootstrap_calendar_days: int = 1300
    cache_hours: int = 18
    adjust: str = "qfq"
    exclude_st: bool = True
    exclude_bj: bool = True
    market_scope: str = "shsz"  # 当前主线：沪深 A 股

    # 扫描
    min_bars: int = 120
    score_threshold: int = 68
    top_n: int = 50
    request_pause_seconds: float = 0.08

    # 回测观察参数：不是选股硬条件
    reference_hold_min_days: int = 5
    reference_hold_max_days: int = 30
    trend_exit_min_days: int = 3

    # 默认历史回测窗口。前置 warmup 只用于指标计算，不计入正式信号区间。
    backtest_calendar_days: int = 540
    backtest_warmup_calendar_days: int = 260

    # 特征有效性研究窗口：先验证再决定是否进入正式评分。
    feature_validation_calendar_days: int = 900
    feature_validation_warmup_calendar_days: int = 260
    feature_validation_train_ratio: float = 0.70

    # V2 候选模型 / Walk-Forward。只在研究层验证通过后才允许进入正式评分。
    v2_max_features: int = 8
    v2_min_feature_samples: int = 200
    v2_min_abs_ic: float = 0.02
    v2_min_abs_return_spread: float = 0.10
    walk_forward_train_bars: int = 252
    walk_forward_validation_bars: int = 63
    walk_forward_gap_bars: int = 5
    walk_forward_step_bars: int = 63
    walk_forward_top_quantile: float = 0.20

    # 风险/收益参数
    stop_atr_multiple: float = 1.8
    target_atr_multiple: float = 3.0

    # 交易成本与组合约束。佣金为默认研究假设，实际账户可自行修改。
    commission_bps: float = 2.5
    min_commission_cny: float = 5.0
    slippage_bps: float = 3.0
    portfolio_initial_capital: float = 1_000_000.0
    portfolio_max_positions: int = 10
    portfolio_max_position_pct: float = 0.10

    # 兼容后备批次；主流程实际批次由 aquant.runtime.resources 按电脑内存动态决定
    flush_every: int = 50


SETTINGS = Settings()


def ensure_directories() -> None:
    SETTINGS.data_store_dir.mkdir(parents=True, exist_ok=True)
    SETTINGS.cache_dir.mkdir(parents=True, exist_ok=True)
    SETTINGS.report_dir.mkdir(parents=True, exist_ok=True)
