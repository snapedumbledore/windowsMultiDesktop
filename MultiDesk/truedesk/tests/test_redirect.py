# -*- coding: utf-8 -*-
"""redirect 切换引擎测试：事务、切换、回滚、崩溃补完（test_mode 模拟后端）。"""
import json
import os

import pytest

from truedesk.config import DesktopConfig
from truedesk.redirect import RegistryBackend, SwitchError, build_backend
from truedesk.single_instance import OperationLock


def _mkdesktop(cm, name, path):
    def _fn(cfg, name=name, path=path):
        cfg.desktops.append(DesktopConfig(id="id_" + name, name=name, path=path))
        if not cfg.current:
            cfg.current = "id_" + name
        if not cfg.default:
            cfg.default = "id_" + name
    cm.mutate(_fn)


def test_registry_backend_apply_verify(tmp_path):
    be = RegistryBackend(test_mode=True)
    target = str(tmp_path / "desk")
    os.makedirs(target, exist_ok=True)
    be.apply(target)
    assert be.verify(target)
    assert be.current_target() == os.path.normpath(target)


def test_backend_factory():
    assert isinstance(build_backend("registry", test_mode=True), RegistryBackend)
    assert build_backend("junction", test_mode=True).name == "junction"


def test_switch_updates_current(cm, engine, store):
    _mkdesktop(cm, "A", str(os.path.join(cm.data_root, "Desktops", "A")))
    _mkdesktop(cm, "B", str(os.path.join(cm.data_root, "Desktops", "B")))
    for d in store.list():
        os.makedirs(d.expanded_path(), exist_ok=True)
    result = engine.switch("B")
    assert result.desktop.name == "B"
    cfg = cm.get()
    assert cfg.current == "id_B"
    assert cfg.pending_switch is None


def test_switch_creates_missing_dir(cm, engine, store):
    _mkdesktop(cm, "A", os.path.join(cm.data_root, "Desktops", "A"))
    result = engine.switch("A")
    assert os.path.isdir(result.desktop.expanded_path())


def test_switch_unknown_desktop(cm, engine):
    with pytest.raises(Exception):
        engine.switch("不存在")


def test_rollback_on_apply_failure(cm, engine, store, monkeypatch):
    _mkdesktop(cm, "A", os.path.join(cm.data_root, "Desktops", "A"))
    os.makedirs(os.path.join(cm.data_root, "Desktops", "A"), exist_ok=True)

    from truedesk.redirect import RegistryBackend

    def boom(self, target_path):
        raise SwitchError("注入失败")

    monkeypatch.setattr(RegistryBackend, "apply", boom)
    with pytest.raises(SwitchError):
        engine.switch("A")
    cfg = cm.get()
    assert cfg.pending_switch is None


def test_complete_or_rollback_rolls_back(cm, engine, store):
    _mkdesktop(cm, "A", os.path.join(cm.data_root, "Desktops", "A"))
    os.makedirs(os.path.join(cm.data_root, "Desktops", "A"), exist_ok=True)
    # 制造一个 pending 但实际未指向目标的事务
    def _seed(cfg):
        cfg.pending_switch = {
            "desktop_id": "id_A",
            "desktop_name": "A",
            "target_path": os.path.join(cm.data_root, "Desktops", "A"),
            "snapshot_path": "",
            "step": "written",
            "timestamp": "t",
        }
    cm.mutate(_seed)
    result = engine.complete_or_rollback()
    assert result is None  # 已回滚
    assert cm.get().pending_switch is None


def test_complete_or_rollback_completes(cm, engine, store):
    _mkdesktop(cm, "A", os.path.join(cm.data_root, "Desktops", "A"))
    os.makedirs(os.path.join(cm.data_root, "Desktops", "A"), exist_ok=True)
    # 先真实切换
    engine.switch("A")
    # 模拟崩溃：pending 存在但 current 未更新
    def _seed(cfg):
        cfg.pending_switch = {
            "desktop_id": "id_A",
            "desktop_name": "A",
            "target_path": os.path.join(cm.data_root, "Desktops", "A"),
            "snapshot_path": "",
            "step": "written",
            "timestamp": "t",
        }
        cfg.current = None
    cm.mutate(_seed)
    result = engine.complete_or_rollback()
    assert result is not None
    assert cm.get().current == "id_A"
    assert cm.get().pending_switch is None


def test_ensure_current_switches_when_mismatch(cm, engine, store):
    _mkdesktop(cm, "A", os.path.join(cm.data_root, "Desktops", "A"))
    os.makedirs(os.path.join(cm.data_root, "Desktops", "A"), exist_ok=True)
    # current 为 A 但后端指向别处
    engine.switch("A")
    engine.backend().apply(os.path.join(cm.data_root, "elsewhere"))
    os.makedirs(os.path.join(cm.data_root, "elsewhere"), exist_ok=True)
    result = engine.ensure_current()
    assert result is not None and result.desktop.name == "A"


def test_operation_lock_roundtrip():
    with OperationLock():
        pass
    with OperationLock():
        pass
