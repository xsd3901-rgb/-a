from __future__ import annotations

from datetime import datetime

from config import SETTINGS, ensure_directories


def report_inventory() -> list[dict]:
    ensure_directories()
    files: list[dict] = []
    for path in sorted(SETTINGS.report_dir.glob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append(
            {
                "文件": path.name,
                "大小KB": round(stat.st_size / 1024.0, 1),
                "更新时间": datetime.fromtimestamp(
                    stat.st_mtime
                ).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return files[-30:]
