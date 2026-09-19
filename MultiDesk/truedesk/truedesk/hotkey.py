# -*- coding: utf-8 -*-
"""全局快捷键：user32.RegisterHotKey 注册 Ctrl+Alt+1..9，消息循环分发 WM_HOTKEY。"""
from __future__ import annotations

import ctypes
import threading
from typing import Callable, Dict, Optional, Tuple

from .logger_setup import get_logger

log = get_logger("hotkey")

_user32 = ctypes.windll.user32
_user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
_user32.RegisterHotKey.restype = ctypes.c_int
_user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.UnregisterHotKey.restype = ctypes.c_int
_user32.GetMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
_user32.GetMessageW.restype = ctypes.c_int
_user32.TranslateMessage.argtypes = [ctypes.c_void_p]
_user32.DispatchMessageW.argtypes = [ctypes.c_void_p]

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002

MOD_MAP = {
    "ctrl": MOD_CONTROL,
    "alt": MOD_ALT,
}


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("pt", ctypes.c_long * 2),
    ]


class HotkeyManager:
    """注册 Ctrl+Alt+<数字键> 热键；在独立线程消息循环中分发。"""

    def __init__(self):
        self._id_to_callback: Dict[int, Callable] = {}
        self._key_to_id: Dict[Tuple[int, int], int] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def parse_modifiers(self, prefix: str) -> int:
        mods = 0
        for part in (prefix or "ctrl+alt").lower().replace(" ", "").split("+"):
            if not part:
                continue
            m = MOD_MAP.get(part)
            if m is None:
                log.warning("不支持的热键修饰键：%s（支持 ctrl/alt）", part)
            else:
                mods |= m
        if not mods:
            mods = MOD_CONTROL | MOD_ALT
        return mods

    def register(self, digit: int, callback: Callable, prefix: str = "ctrl+alt") -> bool:
        """为数字键 1..9 注册热键。返回是否成功。"""
        if not 1 <= digit <= 9:
            return False
        vk = ord("0") + digit
        mods = self.parse_modifiers(prefix)
        key = (mods, vk)
        if key in self._key_to_id:
            return True
        hotkey_id = len(self._key_to_id) + 1
        ok = _user32.RegisterHotKey(None, hotkey_id, mods, vk)
        if not ok:
            log.warning("注册热键 Ctrl+Alt+%d 失败（可能已被占用）", digit)
            return False
        self._key_to_id[key] = hotkey_id
        self._id_to_callback[hotkey_id] = callback
        log.info("已注册热键：Ctrl+Alt+%d", digit)
        return True

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._message_loop, name="hotkey-loop", daemon=True)
        self._thread.start()

    def _message_loop(self) -> None:
        while not self._stop.is_set():
            msg = MSG()
            res = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if res <= 0:
                break
            if msg.message == WM_HOTKEY:
                cb = self._id_to_callback.get(int(msg.wParam))
                if cb:
                    try:
                        cb()
                    except Exception as exc:  # noqa: BLE001
                        log.error("热键回调异常：%s", exc)
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self) -> None:
        self._stop.set()
        try:
            _user32.PostThreadMessageW(self._thread.ident, 0x0012, 0, 0)  # WM_QUIT
        except Exception:  # noqa: BLE001
            pass
        for hid in list(self._id_to_callback.keys()):
            _user32.UnregisterHotKey(None, hid)
        self._id_to_callback.clear()
        self._key_to_id.clear()
