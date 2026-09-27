from __future__ import annotations

import atexit
import threading
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


class BaoStockSession:
    """进程内复用 BaoStock 登录会话。

    BaoStock Python 客户端使用进程级连接状态，因此这里强制串行访问。
    默认每 250 次请求主动重连一次，既避免每只股票反复 login/logout，
    也避免单个长连接无限存活。
    """

    def __init__(self, max_calls: int = 250) -> None:
        self._lock = threading.RLock()
        self._logged_in = False
        self._calls = 0
        self._max_calls = max(1, int(max_calls))

    @staticmethod
    def _module():
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError(
                "缺少 baostock 依赖，请先安装 requirements.txt"
            ) from exc
        return bs

    def _login(self, bs) -> None:
        if self._logged_in:
            return
        login = bs.login()
        if getattr(login, "error_code", "-1") != "0":
            raise RuntimeError(
                f"BaoStock 登录失败: "
                f"{login.error_code} {login.error_msg}"
            )
        self._logged_in = True
        self._calls = 0

    def _logout(self, bs) -> None:
        if not self._logged_in:
            return
        try:
            bs.logout()
        finally:
            self._logged_in = False
            self._calls = 0

    def run(self, callback: Callable[[object], T]) -> T:
        with self._lock:
            bs = self._module()
            self._login(bs)
            try:
                result = callback(bs)
                self._calls += 1
                if self._calls >= self._max_calls:
                    self._logout(bs)
                return result
            except Exception:
                # 网络/协议异常后丢弃当前会话，下次请求重新登录。
                self._logout(bs)
                raise

    def close(self) -> None:
        with self._lock:
            try:
                bs = self._module()
            except Exception:
                self._logged_in = False
                self._calls = 0
                return
            self._logout(bs)


BAOSTOCK_SESSION = BaoStockSession()
atexit.register(BAOSTOCK_SESSION.close)
