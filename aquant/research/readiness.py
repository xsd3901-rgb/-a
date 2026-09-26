from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

from aquant.models.registry import model_status
from aquant.research.data_audit import run_data_audit
from aquant.version import __version__
from config import SETTINGS, ensure_directories


REQUIRED_PROJECT_FILES = (
    "main.py",
    "webapp.py",
    "scanner.py",
    "backtest.py",
    "strategy.py",
    "config.py",
    "requirements.txt",
)

REQUIRED_PACKAGES = (
    "pandas",
    "numpy",
    "flask",
    "duckdb",
    "pyarrow",
    "requests",
    "psutil",
    "akshare",
    "baostock",
    "openpyxl",
)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def release_readiness(
    *,
    audit_limit: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """检查“代码、环境、本地数据、研究验收”四个层面是否就绪。

    这里只做本地检查，不触发外网行情下载，也不会自动修改模型。
    """
    ensure_directories()
    rows: list[dict] = []

    missing_files = [
        name for name in REQUIRED_PROJECT_FILES
        if not (SETTINGS.project_root / name).exists()
    ]
    rows.append(
        {
            "检查项": "项目文件",
            "状态": "OK" if not missing_files else "FAIL",
            "说明": (
                "核心项目文件齐全"
                if not missing_files
                else "缺少: " + "、".join(missing_files)
            ),
        }
    )

    missing_packages = [
        name for name in REQUIRED_PACKAGES
        if importlib.util.find_spec(name) is None
    ]
    rows.append(
        {
            "检查项": "Python依赖",
            "状态": "OK" if not missing_packages else "FAIL",
            "说明": (
                "核心依赖齐全"
                if not missing_packages
                else "缺少: " + "、".join(missing_packages)
            ),
        }
    )

    try:
        _, audit_summary = run_data_audit(limit=audit_limit)
        audit_state = str(audit_summary.get("状态", "未知"))
        audit_ok = audit_state in {"通过", "可用但有警告"}
        rows.append(
            {
                "检查项": "本地行情库",
                "状态": "OK" if audit_ok else "PENDING",
                "说明": (
                    f"{audit_state} | 检查 {audit_summary.get('检查股票数', 0)} 只 | "
                    f"FAIL {audit_summary.get('FAIL', 0)} | "
                    f"覆盖率中位数 {audit_summary.get('覆盖率中位数%', 0)}%"
                ),
            }
        )
    except Exception as exc:
        audit_summary = {}
        rows.append(
            {
                "检查项": "本地行情库",
                "状态": "PENDING",
                "说明": f"尚未完成本地建库/审计: {str(exc)[:240]}",
            }
        )

    validation_summary = _read_json(
        SETTINGS.report_dir / "system_validation_summary.json"
    )
    validation_state = str(validation_summary.get("总体状态", "尚未运行"))
    validation_ok = validation_state.startswith("系统链路通过")
    rows.append(
        {
            "检查项": "系统研究验收",
            "状态": "OK" if validation_ok else "PENDING",
            "说明": validation_state,
        }
    )

    models = model_status()
    rows.append(
        {
            "检查项": "活动模型",
            "状态": "OK",
            "说明": (
                f"正式模型 {models.get('active_model', 'V1')} | "
                f"V2 {models.get('v2_state', '尚未验证')} | "
                "不会自动晋级"
            ),
        }
    )

    report = pd.DataFrame(rows)
    hard_fail = bool((report["状态"] == "FAIL").any())
    pending = bool((report["状态"] == "PENDING").any())

    if hard_fail:
        overall = "项目环境未就绪"
        next_action = "先补齐项目文件或 Python 依赖。"
    elif pending:
        data_state = next(
            (
                row["状态"]
                for row in rows
                if row["检查项"] == "本地行情库"
            ),
            "PENDING",
        )
        validation_check = next(
            (
                row["状态"]
                for row in rows
                if row["检查项"] == "系统研究验收"
            ),
            "PENDING",
        )
        if data_state != "OK":
            overall = "代码已就绪，等待本地建库"
            next_action = "先运行 python main.py bootstrap，再运行 audit-data。"
        elif validation_check != "OK":
            overall = "数据已就绪，等待正式验收"
            next_action = (
                "运行 python main.py validate-system --limit 200 --horizon 10；"
                "全市场数据稳定后再把 limit 扩大。"
            )
        else:
            overall = "等待其他检查"
            next_action = "查看 release_readiness.csv 中的 PENDING 项。"
    else:
        overall = "本地运行链路已就绪"
        next_action = (
            "可以启动网页终端进行扫描；模型结果仍属于量化研究，"
            "不代表未来收益。"
        )

    summary = {
        "版本": __version__,
        "总体状态": overall,
        "下一步": next_action,
        "项目目录": str(SETTINGS.project_root),
        "数据目录": str(SETTINGS.data_store_dir),
        "报告目录": str(SETTINGS.report_dir),
        "活动模型": models.get("active_model", "V1"),
        "V2状态": models.get("v2_state", "尚未验证"),
        "自动晋级": False,
    }

    report.to_csv(
        SETTINGS.report_dir / "release_readiness.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (SETTINGS.report_dir / "release_readiness.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report, summary
