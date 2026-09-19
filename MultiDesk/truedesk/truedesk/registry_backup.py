# -*- coding: utf-8 -*-
"""注册表备份/恢复：桌面路径原值备份、一键恢复默认桌面。"""
from __future__ import annotations

import json
import os
import time
import winreg
from typing import Dict, Optional, Tuple

from .constants import (REG_DESKTOP_NAME, REG_SHELL_FOLDERS, REG_USER_SHELL_FOLDERS)
from .logger_setup import get_logger

log = get_logger("registry_backup")


class RegistryError(Exception):
    """注册表操作错误。"""


# ---------- 桌面路径读取 / 写入 ----------
def read_desktop_paths() -> Dict[str, str]:
    """读取 User Shell Folders\\Desktop 与 Shell Folders\\Desktop 两个键的原值。"""
    result = {}
    for hive_key, name in (
        (REG_USER_SHELL_FOLDERS, "user_shell_folders"),
        (REG_SHELL_FOLDERS, "shell_folders"),
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, hive_key, 0, winreg.KEY_READ) as k:
                value, _ = winreg.QueryValueEx(k, REG_DESKTOP_NAME)
                result[name] = value
        except OSError:
            result[name] = ""
    return result


def write_desktop_paths(user_value: str, shell_value: str) -> None:
    """同时写入两个键的 Desktop 值（失败回滚由调用方负责）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_USER_SHELL_FOLDERS, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, REG_DESKTOP_NAME, 0, winreg.REG_EXPAND_SZ, user_value)
    except OSError as exc:
        raise RegistryError("写入 User Shell Folders\\Desktop 失败：%s" % exc)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_SHELL_FOLDERS, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, REG_DESKTOP_NAME, 0, winreg.REG_SZ, shell_value)
    except OSError as exc:
        raise RegistryError("写入 Shell Folders\\Desktop 失败：%s" % exc)
    log.info("桌面路径已重定向：user=%s shell=%s", user_value, shell_value)


# ---------- 备份 ----------
class RegistryBackup:
    """注册表原值备份与恢复（支持注入 test_mode）。"""

    def __init__(self, data_root: str, test_mode: bool = False):
        self.data_root = data_root
        self.test_mode = test_mode
        self.backup_root = os.path.join(data_root, "backup")
        os.makedirs(self.backup_root, exist_ok=True)
        self._fake: Optional[Dict[str, str]] = None  # test_mode 下的模拟值

    def _read(self) -> Dict[str, str]:
        if self.test_mode:
            return dict(self._fake or {})
        return read_desktop_paths()

    def _write(self, user_value: str, shell_value: str) -> None:
        if self.test_mode:
            self._fake = {
                "user_shell_folders": user_value,
                "shell_folders": shell_value,
            }
            return
        write_desktop_paths(user_value, shell_value)

    def snapshot_path(self, ts: Optional[str] = None) -> str:
        ts = ts or time.strftime("%Y%m%d_%H%M%S")
        return os.path.join(self.backup_root, "registry_backup_%s.json" % ts)

    def backup(self) -> str:
        """备份当前桌面路径原值，返回备份文件路径。"""
        paths = self._read()
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "user_shell_folders_desktop": paths.get("user_shell_folders", ""),
            "shell_folders_desktop": paths.get("shell_folders", ""),
            "note": "恢复命令：truedesk restore",
        }
        path = self.snapshot_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log.info("已备份桌面路径：%s", path)
        return path

    def restore_backup(self, path: Optional[str] = None) -> str:
        """从指定（或最新）备份恢复桌面路径。"""
        path = path or self.latest_backup()
        if not path or not os.path.exists(path):
            raise RegistryError("未找到可用的注册表备份文件")
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        user_value = payload.get("user_shell_folders_desktop", "%USERPROFILE%\\Desktop")
        shell_value = payload.get("shell_folders_desktop", "")
        if not shell_value:
            shell_value = os.path.expandvars(user_value)
        self._write(user_value, shell_value)
        log.info("已从备份恢复桌面路径：%s", path)
        return path

    def latest_backup(self) -> Optional[str]:
        if not os.path.isdir(self.backup_root):
            return None
        files = [f for f in os.listdir(self.backup_root) if f.startswith("registry_backup_") and f.endswith(".json")]
        if not files:
            return None
        files.sort(reverse=True)
        return os.path.join(self.backup_root, files[0])

    def restore_default(self) -> None:
        """恢复为系统默认路径 %USERPROFILE%\\Desktop。"""
        user_value = "%USERPROFILE%\\Desktop"
        shell_value = os.path.join(os.path.expandvars("%USERPROFILE%"), "Desktop")
        self._write(user_value, shell_value)
        log.info("桌面路径已恢复为系统默认")
