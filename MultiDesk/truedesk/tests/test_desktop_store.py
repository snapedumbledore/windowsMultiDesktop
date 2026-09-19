# -*- coding: utf-8 -*-
"""desktop_store 测试：创建/重命名/删除/排序/默认桌面。"""
import os

import pytest

from truedesk.desktop_store import (DesktopError, DesktopExistsError, DesktopManager,
                                    DesktopNotFoundError, InvalidNameError,
                                    LastDesktopError)


def test_create_defaults(store):
    d = store.create("工作")
    assert d.name == "工作"
    assert d.sort_order == 1
    assert os.path.isdir(d.expanded_path())
    assert store.current().id == d.id
    assert store.default().id == d.id


def test_create_custom_path(store, tmp_path):
    target = tmp_path / "custom_desk"
    d = store.create("游戏", path=str(target))
    assert os.path.isdir(str(target))
    assert d.expanded_path() == str(target).replace("/", "\\") or d.expanded_path()


def test_invalid_names(store):
    for bad in ("", "  ", "a/b", "c:d", 'x"y', "a<b"):
        with pytest.raises(InvalidNameError):
            store.create(bad)


def test_duplicate_name(store):
    store.create("工作")
    with pytest.raises(DesktopExistsError):
        store.create("工作")


def test_sort_order_increments(store):
    store.create("A")
    store.create("B")
    store.create("C")
    names = [d.name for d in store.list()]
    assert names == ["A", "B", "C"]


def test_rename_only(store):
    d = store.create("旧名")
    old_path = d.expanded_path()
    store.rename(d.id, "新名")
    nd = store.get("新名")
    assert nd.name == "新名"
    assert nd.expanded_path() == old_path
    assert os.path.isdir(old_path)


def test_rename_with_migrate(store):
    d = store.create("旧名")
    old_path = d.expanded_path()
    store.rename(d.id, "新名", migrate=True)
    nd = store.get("新名")
    assert nd.expanded_path() != old_path
    assert os.path.isdir(nd.expanded_path())
    assert not os.path.exists(old_path)


def test_delete_keeps_folder(store):
    store.create("保留")
    d = store.create("临时")
    path = d.expanded_path()
    store.delete(d.id, delete_folder=False)
    assert os.path.isdir(path)
    with pytest.raises(DesktopNotFoundError):
        store.get("临时")


def test_delete_with_folder(store):
    store.create("保留2")
    d = store.create("临时2")
    path = d.expanded_path()
    store.delete(d.id, delete_folder=True)
    assert not os.path.exists(path)


def test_delete_last_desktop_blocked(store):
    store.create("唯一")
    with pytest.raises(LastDesktopError):
        store.delete("唯一")


def test_current_fallback_after_delete(store):
    a = store.create("A")
    store.create("B")
    store.delete(a.id)
    cur = store.current()
    assert cur is not None and cur.name == "B"


def test_set_default(store):
    a = store.create("A")
    b = store.create("B")
    store.set_default(b.id)
    assert store.default().id == b.id
    assert store.default().id != a.id


def test_delete_missing_is_idempotent(store):
    # 桌面已不存在时删除应幂等成功，而不是抛“逻辑桌面不存在”
    r = store.delete("不存在的桌面")
    assert r["deleted"] is False
    assert r["already_gone"] is True
    assert r["folder_warning"] is None


def test_delete_returns_result_dict(store):
    store.create("保留")
    d = store.create("待删")
    r = store.delete(d.id, delete_folder=False)
    assert r["deleted"] is True
    assert r["already_gone"] is False
    assert r["name"] == "待删"
    assert r["folder_warning"] is None
    with pytest.raises(DesktopNotFoundError):
        store.get("待删")


def test_cache_invalidated_after_external_write(data_root):
    # 模拟另一个进程改写 config：长驻进程的内存缓存应按文件 mtime 自动失效
    from truedesk.config import ConfigManager
    from truedesk.desktop_store import DesktopManager
    root = os.path.join(data_root, "Desktops")
    cm1 = ConfigManager(data_root=data_root)
    cm1.ensure_dirs()
    s1 = DesktopManager(cm1, desktop_root=root)
    s1.create("A")
    assert cm1.get().restore_on_exit is True  # 预热缓存

    cm2 = ConfigManager(data_root=data_root)
    cm2.mutate(lambda c: setattr(c, "restore_on_exit", False))

    assert cm1.get().restore_on_exit is False  # 自动读到磁盘最新


def test_delete_picks_up_externally_created_desktop(data_root):
    # 旧 bug：长驻进程缓存过期后删除会报“不存在”，或整体写回覆盖掉新桌面
    from truedesk.config import ConfigManager
    from truedesk.desktop_store import DesktopManager
    root = os.path.join(data_root, "Desktops")
    cm1 = ConfigManager(data_root=data_root)
    cm1.ensure_dirs()
    s1 = DesktopManager(cm1, desktop_root=root)
    s1.create("A")
    cm1.get()  # 预热缓存（此时只知道 A）

    # 另一个进程创建了 B
    cm2 = ConfigManager(data_root=data_root)
    s2 = DesktopManager(cm2, desktop_root=root)
    b = s2.create("B")

    # 长驻进程应能看到 B，并基于磁盘最新状态删除 B，且不丢失 A
    assert "B" in [d.name for d in s1.list()]
    r = s1.delete(b.id)
    assert r["deleted"] is True
    names = [d.name for d in s1.list()]
    assert "B" not in names
    assert "A" in names


def test_force_remove_tree_readonly_dir(tmp_path):
    # Windows 桌面目录常带 ReadOnly 属性；健壮删除应能清掉只读位并删干净
    import os as _os
    import stat as _stat
    from truedesk.desktop_store import _force_remove_tree
    d = tmp_path / "ro_tree"
    d.mkdir()
    f = d / "read_only.lnk"
    f.write_text("x", encoding="utf-8")
    _os.chmod(f, _stat.S_IREAD)      # 只读文件
    _os.chmod(d, _stat.S_IREAD)      # 只读目录（模拟桌面目录）
    _force_remove_tree(str(d))
    assert not d.exists()
