# -*- coding: utf-8 -*-
"""单实例互斥与跨进程操作锁（CreateMutexW）。"""
from __future__ import annotations

import ctypes
import time
from typing import Optional

from .logger_setup import get_logger

log = get_logger("single_instance")

_kernel32 = ctypes.windll.kernel32
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
_kernel32.WaitForSingleObject.restype = ctypes.c_uint
_kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
_kernel32.ReleaseMutex.restype = ctypes.c_int
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.restype = ctypes.c_int

ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_ABANDONED = 0x80
INFINITE = 0xFFFFFFFF


class SingleInstance:
    """应用级单实例：已存在实例时 acquire 返回 False。"""

    def __init__(self, name: str = "TrueDesk.SingleInstance"):
        self.name = name
        self._handle: Optional[int] = None

    def acquire(self) -> bool:
        handle = _kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            log.warning("创建互斥体失败")
            return False
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            log.info("检测到 TrueDesk 已在运行")
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle:
            _kernel32.ReleaseMutex(self._handle)
            _kernel32.CloseHandle(self._handle)
            self._handle = None


class OperationLock:
    """跨进程操作锁：串行化注册表/桌面切换等写操作。

    任意入口（托盘/CLI/GUI）执行写操作前获取，避免并发写注册表。
    """

    def __init__(self, name: str = "TrueDesk.OperationLock", timeout: float = 20.0):
        self.name = name
        self.timeout = timeout
        self._handle: Optional[int] = None

    def __enter__(self) -> "OperationLock":
        handle = _kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise RuntimeError("无法创建操作锁")
        self._handle = handle
        deadline = time.monotonic() + self.timeout
        while True:
            res = _kernel32.WaitForSingleObject(handle, 0)
            if res == WAIT_OBJECT_0:
                return self
            if res == WAIT_ABANDONED:
                log.warning("操作锁被占用后废弃，重新获得")
                return self
            if time.monotonic() >= deadline:
                _kernel32.CloseHandle(handle)
                self._handle = None
                raise TimeoutError("等待 TrueDesk 操作锁超时，可能已有切换正在进行")
            time.sleep(0.1)

    def __exit__(self, *exc) -> None:
        if self._handle:
            _kernel32.ReleaseMutex(self._handle)
            _kernel32.CloseHandle(self._handle)
            self._handle = None
