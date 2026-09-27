from __future__ import annotations

from aquant.web import create_app
from config import SETTINGS, ensure_directories


app = create_app()


if __name__ == "__main__":
    ensure_directories()
    print("=" * 68)
    print("A-Quant 沪深A股量化终端")
    print(f"项目目录: {SETTINGS.project_root}")
    print(f"本地数据: {SETTINGS.data_store_dir}")
    print(f"报告目录: {SETTINGS.report_dir}")
    print("浏览器地址: http://127.0.0.1:5000")
    print("=" * 68)
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        threaded=True,
    )
