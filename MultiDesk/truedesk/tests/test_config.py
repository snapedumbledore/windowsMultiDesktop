# -*- coding: utf-8 -*-
"""config 模块测试：读写、校验、损坏处理、环境变量展开。"""
import json
import os

import pytest

from truedesk.config import (AppConfig, ConfigError, ConfigManager, DesktopConfig,
                             expand_path)


def test_empty_config_creates_default(cm):
    cfg = cm.load()
    assert cfg.version == 1
    assert cfg.desktops == []
    assert os.path.exists(cm.config_path)


def test_save_and_reload(cm):
    cfg = cm.load()
    cfg.desktops.append(DesktopConfig(id="d1", name="工作", path=r"%USERPROFILE%\TrueDesk\Desktops\工作"))
    cm.save(cfg)
    cfg2 = cm.reload()
    assert len(cfg2.desktops) == 1
    assert cfg2.desktops[0].name == "工作"


def test_corrupted_config_raises(cm):
    with open(cm.config_path, "w", encoding="utf-8") as f:
        f.write("{ not valid json !!!")
    with pytest.raises(ConfigError):
        cm.load()


def test_no_desktops_raises(cm):
    cfg = AppConfig()
    with open(cm.config_path, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, ensure_ascii=False)
    with pytest.raises(ConfigError):
        cm.load()


def test_redirect_mode_validated(cm):
    cfg = AppConfig()
    cfg.redirect_mode = "bogus"
    cfg.explorer_refresh = "weird"
    cfg.desktops.append(DesktopConfig(id="d1", name="x", path="/tmp/x"))
    with open(cm.config_path, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, ensure_ascii=False)
    loaded = cm.reload()
    assert loaded.redirect_mode == "registry"
    assert loaded.explorer_refresh == "auto"


def test_expand_path():
    assert expand_path("%USERPROFILE%\\Desktop").startswith(os.path.expandvars("%USERPROFILE%"))
    assert expand_path("") == ""


def test_mutate_atomic(cm):
    cm.mutate(lambda c: c.desktops.append(
        DesktopConfig(id="a", name="A", path=r"%USERPROFILE%\TrueDesk\Desktops\A")))
    cfg = cm.get()
    assert len(cfg.desktops) == 1
