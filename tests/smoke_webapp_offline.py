from __future__ import annotations

from webapp import app


def main() -> None:
    client = app.test_client()

    root = client.get("/")
    assert root.status_code == 200
    text = root.get_data(as_text=True)
    assert "A-Quant" in text
    assert "一键系统验收" in text
    assert "建立/更新本地数据库" in text
    assert "本地数据审计" in text
    assert "就绪检查" in text

    status = client.get("/api/status")
    assert status.status_code == 200
    payload = status.get_json()
    assert payload["ok"] is True
    assert "runtime" in payload
    assert "profile" in payload
    assert "paths" in payload

    profile = client.get("/api/profile")
    assert profile.status_code == 200
    assert profile.get_json()["ok"] is True

    print("OFFLINE_WEBAPP_OK")


if __name__ == "__main__":
    main()
