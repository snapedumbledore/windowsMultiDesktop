# -*- coding: utf-8 -*-
"""公共桌面隔离测试：路径/副本/开关/切换同步/pending 协议/恢复（全程 test_mode，不触碰真实注册表）。"""
import json
import os

import pytest

from truedesk import isolation
from truedesk.isolation import (PublicIsolation, apply_pending, default_common_values,
                                ensure_public_dir, load_original, plan_pending,
                                public_dir, save_original, write_common_desktop)


@pytest.fixture(autouse=True)
def _reset_fake():
    isolation.reset_fake_for_tests()
    yield
    isolation.reset_fake_for_tests()


def _enable(cm):
    iso = PublicIsolation(cm, test_mode=True)
    msg = iso.enable(elevated=True)
    return iso, msg


def test_public_dir_path(data_root):
    assert public_dir(data_root, "abc") == os.path.join(data_root, "Public", "abc")


def test_default_common_values():
    v = default_common_values()
    assert v["user_shell_folders_common"] == "%PUBLIC%\\Desktop"
    assert v["shell_folders_common"] == os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop")


def test_ensure_public_dir_copies_source(tmp_path, monkeypatch, cm):
    src = tmp_path / "PublicSrc"
    src.mkdir()
    (src / "a.lnk").write_text("a", encoding="utf-8")
    (src / "b.txt").write_text("b", encoding="utf-8")
    (src / "desktop.ini").write_text("ini", encoding="utf-8")
    monkeypatch.setattr(isolation, "PUBLIC_DESKTOP_SOURCE", str(src))

    # 默认（新建桌面）：只建空目录，不复制系统公共图标
    target_empty = ensure_public_dir(cm.data_root, "d_empty")
    assert os.listdir(target_empty) == []

    # copy_initial=True（首次启用隔离）：复制一次图标
    target = ensure_public_dir(cm.data_root, "d1", copy_initial=True)
    names = os.listdir(target)
    assert "a.lnk" in names and "b.txt" in names
    assert "desktop.ini" not in names
    # 源不被删除
    assert (src / "a.lnk").exists()


def test_enable_sets_isolated_and_hklm(cm, store):
    store.create("工作", path=os.path.join(cm.data_root, "Desktops", "工作"))
    iso, msg = _enable(cm)
    assert cm.get().public_redirect_mode == "isolated"
    cur = cm.get().current
    expect = public_dir(cm.data_root, cur)
    assert isolation.read_common_desktop()["shell_folders_common"] == os.path.normpath(expect)
    assert os.path.isdir(expect)
    assert "已启用" in msg


def test_enable_idempotent(cm, store):
    store.create("A", path=os.path.join(cm.data_root, "Desktops", "A"))
    _enable(cm)
    iso = PublicIsolation(cm, test_mode=True)
    assert "已启用" in iso.enable(elevated=True)


def test_disable_restores_default(cm, store):
    store.create("A", path=os.path.join(cm.data_root, "Desktops", "A"))
    _enable(cm)
    iso = PublicIsolation(cm, test_mode=True)
    msg = iso.disable()
    assert cm.get().public_redirect_mode == "none"
    values = isolation.read_common_desktop()
    assert values["shell_folders_common"] == os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop")
    assert "已关闭" in msg


def test_switch_syncs_public_desktop(cm, engine, store):
    store.create("A", path=os.path.join(cm.data_root, "Desktops", "A"))
    store.create("B", path=os.path.join(cm.data_root, "Desktops", "B"))
    for d in store.list():
        os.makedirs(d.expanded_path(), exist_ok=True)
    _enable(cm)
    engine.switch("B")
    b = store.get("B")
    assert isolation.read_common_desktop()["shell_folders_common"] == os.path.normpath(
        public_dir(cm.data_root, b.id))
    engine.switch("A")
    a = store.get("A")
    assert isolation.read_common_desktop()["shell_folders_common"] == os.path.normpath(
        public_dir(cm.data_root, a.id))


def test_apply_pending_apply(cm, tmp_path):
    target = tmp_path / "pub"
    target.mkdir()
    plan_pending(cm.data_root, "apply", str(target))
    assert os.path.exists(os.path.join(cm.data_root, "pending_public.json"))
    result = apply_pending(cm.data_root, test_mode=True)
    assert "已指向" in result
    assert not os.path.exists(os.path.join(cm.data_root, "pending_public.json"))
    assert isolation.read_common_desktop()["shell_folders_common"] == os.path.normpath(str(target))


def test_apply_pending_restore_uses_original(cm, tmp_path):
    target = tmp_path / "pub"
    target.mkdir()
    # 先备份当前原值，再改路径，再 restore
    orig = isolation.read_common_desktop()
    save_original(cm.data_root)
    write_common_desktop("C:\\tmp\\pub", "C:\\tmp\\pub")
    plan_pending(cm.data_root, "restore")
    apply_pending(cm.data_root, test_mode=True)
    values = isolation.read_common_desktop()
    assert values["shell_folders_common"] == orig["shell_folders_common"]


def test_apply_pending_none(cm):
    assert apply_pending(cm.data_root, test_mode=True) is None


def test_config_roundtrip_public_mode(cm, store):
    store.create("A", path=os.path.join(cm.data_root, "Desktops", "A"))
    _enable(cm)
    cm.reload()
    assert cm.get().public_redirect_mode == "isolated"


def test_create_auto_public_dir(cm, store):
    store.create("A", path=os.path.join(cm.data_root, "Desktops", "A"))
    _enable(cm)
    d = store.create("新桌面", path=os.path.join(cm.data_root, "Desktops", "新桌面"))
    assert os.path.isdir(public_dir(cm.data_root, d.id))


def test_recovery_resyncs_public(cm, store):
    from truedesk.recovery import RecoveryRunner
    store.create("A", path=os.path.join(cm.data_root, "Desktops", "A"))
    _enable(cm)
    a = store.get("A")
    # 模拟公共桌面指向被外部改到别处
    write_common_desktop("C:\\elsewhere", "C:\\elsewhere")
    RecoveryRunner(cm, test_mode=True)._recover_public_desktop()
    assert isolation.read_common_desktop()["shell_folders_common"] == os.path.normpath(
        public_dir(cm.data_root, a.id))


def test_load_original_fallback_default(data_root):
    v = load_original(data_root)
    assert v["user_shell_folders_common"] == "%PUBLIC%\\Desktop"
