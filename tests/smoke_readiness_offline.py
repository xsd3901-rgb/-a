from __future__ import annotations

from aquant.research.readiness import release_readiness


def main() -> None:
    report, summary = release_readiness(audit_limit=1)

    assert not report.empty
    checks = set(report["检查项"].astype(str))
    assert "项目文件" in checks
    assert "Python依赖" in checks
    assert "本地行情库" in checks
    assert "系统研究验收" in checks
    assert "活动模型" in checks

    project_row = report[report["检查项"] == "项目文件"].iloc[0]
    deps_row = report[report["检查项"] == "Python依赖"].iloc[0]
    assert project_row["状态"] == "OK"
    assert deps_row["状态"] == "OK"

    assert "总体状态" in summary
    assert "下一步" in summary
    assert summary["活动模型"] == "V1"
    assert summary["自动晋级"] is False

    print("OFFLINE_READINESS_OK")


if __name__ == "__main__":
    main()
