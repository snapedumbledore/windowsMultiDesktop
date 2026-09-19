# -*- coding: utf-8 -*-
"""pytest 共享夹具：临时数据目录与配置管理（不触碰真实注册表/桌面）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from truedesk.config import ConfigManager  # noqa: E402


@pytest.fixture()
def data_root(tmp_path):
    return str(tmp_path / "TrueDesk")


@pytest.fixture()
def cm(data_root):
    """指向临时目录的 ConfigManager（测试专用）。"""
    manager = ConfigManager(data_root=data_root)
    manager.ensure_dirs()
    return manager


@pytest.fixture()
def store(cm):
    from truedesk.desktop_store import DesktopManager
    return DesktopManager(cm, desktop_root=os.path.join(cm.data_root, "Desktops"))


@pytest.fixture()
def engine(cm):
    from truedesk.redirect import RedirectEngine
    return RedirectEngine(cm, test_mode=True, state_delay=0.0)


@pytest.fixture()
def backup(cm):
    from truedesk.registry_backup import RegistryBackup
    return RegistryBackup(cm.data_root, test_mode=True)
