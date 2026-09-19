# -*- coding: utf-8 -*-
"""registry_backup 测试（test_mode 模拟，不触碰真实注册表）。"""
import json
import os

import pytest

from truedesk.registry_backup import RegistryBackup, RegistryError


def _seed(backup, user=r"%USERPROFILE%\Desktop", shell=None):
    backup._fake = {
        "user_shell_folders": user,
        "shell_folders": shell or os.path.join(os.path.expandvars("%USERPROFILE%"), "Desktop"),
    }


def test_backup_creates_json(backup):
    _seed(backup)
    path = backup.backup()
    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["user_shell_folders_desktop"] == r"%USERPROFILE%\Desktop"


def test_restore_backup(backup):
    _seed(backup, user=r"%USERPROFILE%\Desktop")
    backup.backup()
    # 模拟切换到逻辑桌面
    backup._fake = {"user_shell_folders": r"C:\TrueDesk\Desktops\工作",
                    "shell_folders": r"C:\TrueDesk\Desktops\工作"}
    backup.restore_backup()
    assert backup._fake["user_shell_folders"] == r"%USERPROFILE%\Desktop"


def test_latest_backup(backup):
    _seed(backup)
    p1 = backup.snapshot_path("20260101_000000")
    with open(p1, "w", encoding="utf-8") as f:
        json.dump({"a": 1}, f)
    p2 = backup.snapshot_path("20260102_000000")
    with open(p2, "w", encoding="utf-8") as f:
        json.dump({"a": 2}, f)
    assert backup.latest_backup() == p2


def test_restore_default(backup):
    _seed(backup)
    backup.restore_default()
    assert backup._fake["user_shell_folders"] == "%USERPROFILE%\\Desktop"
    assert backup._fake["shell_folders"].endswith("Desktop")


def test_no_backup_raises(backup):
    with pytest.raises(RegistryError):
        backup.restore_backup()
