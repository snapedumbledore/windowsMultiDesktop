# -*- coding: utf-8 -*-
"""开机自启：HKCU\\...\\Run 写入 TrueDesk 项（--restore 恢复上次/默认桌面）。"""
from __future__ import annotations

import os
import sys
import winreg
from typing import Optional

from .constants import APP_NAME, REG_RUN
from .logger_setup import get_logger

log = get_logger("autostart")


def current_executable() -> str:
    """当前程序路径（PyInstaller 打包后为 exe 路径）。"""
    exe = getattr(sys, "frozen", None)
    if exe:
        return sys.executable
    # 开发模式：直接使用 python 运行本包入口
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "__main__.py")


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(value)
    except OSError:
        return False


def enable(extra_args: str = "--restore") -> None:
    """写入开机自启项。"""
    cmd = '"%s" %s' % (current_executable(), extra_args)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        log.info("已启用开机自启：%s", cmd)
    except OSError as exc:
        raise OSError("写入开机自启失败：%s" % exc)


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
        log.info("已禁用开机自启")
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise OSError("删除开机自启失败：%s" % exc)


def set_enabled(enabled: bool) -> None:
    if enabled:
        enable()
    else:
        disable()
