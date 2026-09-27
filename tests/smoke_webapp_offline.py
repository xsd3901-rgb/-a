from __future__ import annotations

from webapp import app


def main() -> None:
    client = app.test_client()

    root = client.get("/")
    assert root.status_code == 200
    text = root.get_data(as_text=True)
    assert "A-Quant" in text
    assert "1.0.0-rc1" in text
    assert "一键系统验收" in text
    assert "更新本地数据库" in text
    assert "数据完整性审计" in text
    assert "定向修复失败" in text
    assert "就绪检查" in text
    assert "首次完整准备" in text
    assert "数据中心" in text
    assert "策略研究" in text
    assert "数据源质量追踪" in text
    assert "V1规则消融" in text
    assert "progressBar" in text
    assert "近期组合权益" in text

    status = client.get("/api/status")
    assert status.status_code == 200
    payload = status.get_json()
    assert payload["ok"] is True
    assert payload["version"] == "1.0.0-rc1"
    assert "runtime" in payload
    assert "profile" in payload
    assert "paths" in payload

    profile = client.get("/api/profile")
    assert profile.status_code == 200
    assert profile.get_json()["ok"] is True

    print("OFFLINE_WEBAPP_OK")


if __name__ == "__main__":
    main()
