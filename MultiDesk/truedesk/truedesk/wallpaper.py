# -*- coding: utf-8 -*-
"""独立壁纸：SystemParametersInfoW 设置 + WallPaper 键持久化。"""
from __future__ import annotations

import ctypes
import os
import winreg
from typing import Optional

from .constants import (REG_CONTROL_DESKTOP, REG_WALLPAPER_NAME, SPIF_SENDCHANGE,
                        SPIF_UPDATEINIFILE, SPI_SETDESKWALLPAPER)
from .logger_setup import get_logger

log = get_logger("wallpaper")

SUPPORTED_EXTS = (".jpg", ".jpeg", ".bmp", ".png")

_user32 = ctypes.windll.user32


class WallpaperError(Exception):
    pass


def validate_wallpaper_file(path: str) -> Optional[str]:
    if not path:
        return None
    path = os.path.abspath(os.path.expandvars(path))
    if not os.path.isfile(path):
        raise WallpaperError("壁纸文件不存在：%s" % path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTS:
        raise WallpaperError("不支持的壁纸格式：%s（支持 jpg/jpeg/bmp/png）" % ext)
    return path


def set_wallpaper(path: str) -> bool:
    """设置系统壁纸并持久化到注册表。"""
    real = validate_wallpaper_file(path)
    if real is None:
        return False
    # 1) 注册表持久化
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_CONTROL_DESKTOP, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, REG_WALLPAPER_NAME, 0, winreg.REG_SZ, real)
    except OSError as exc:
        log.warning("写入壁纸注册表失败（不影响本次设置）：%s", exc)
    # 2) 立即应用
    result = _user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, real,
                                           SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
    if not result:
        log.warning("SystemParametersInfoW 设置壁纸失败：%s", real)
        return False
    log.info("壁纸已应用：%s", real)
    return True


def get_current_wallpaper() -> Optional[str]:
    """读取当前系统壁纸路径（注册表 WallPaper）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_CONTROL_DESKTOP, 0,
                            winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, REG_WALLPAPER_NAME)
            return value or None
    except OSError:
        return None


def apply_desktop_wallpaper(wallpaper: Optional[str], enabled: bool) -> bool:
    """切换时应用目标桌面壁纸：enabled 且路径有效才设置。"""
    if not enabled or not wallpaper:
        return False
    try:
        return set_wallpaper(wallpaper)
    except WallpaperError as exc:
        log.warning("应用壁纸失败：%s", exc)
        return False
