# -*- coding: utf-8 -*-
"""cli 测试：参数解析与不触碰注册表的命令（export/import/list）。"""
import json
import os

from truedesk.cli import build_parser, run
from truedesk.config import DesktopConfig
from truedesk.desktop_store import DesktopManager
from truedesk.redirect import RedirectEngine


def _parser_cmd(argv):
    return build_parser().parse_args(argv)


def test_parser_subcommands():
    assert _parser_cmd(["switch", "工作"]).command == "switch"
    assert _parser_cmd(["create", "游戏", "--path", "D:/x"]).path == "D:/x"
    assert _parser_cmd(["rename", "a", "b", "--migrate"]).migrate is True
    assert _parser_cmd(["delete", "a"]).delete_folder is False
    assert _parser_cmd(["delete", "a", "--delete-folder"]).delete_folder is True


def test_list_runs(cm, engine, store):
    store.create("工作")
    store.create("游戏")
    args = _parser_cmd(["list"])
    assert run(args, cm, engine) == 0


def test_export_import_roundtrip(cm, engine, store, tmp_path):
    store.create("工作")
    export_path = str(tmp_path / "export.json")
    assert run(_parser_cmd(["export", export_path]), cm, engine) == 0
    assert os.path.exists(export_path)
    with open(export_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["app"] == "TrueDesk"
    assert len(payload["desktops"]) == 1

    # 导入到另一个空配置
    from truedesk.config import ConfigManager
    cm2 = ConfigManager(data_root=str(tmp_path / "new_root"))
    cm2.ensure_dirs()
    store2 = DesktopManager(cm2)
    engine2 = RedirectEngine(cm2, test_mode=True, state_delay=0.0)
    assert run(_parser_cmd(["import", export_path]), cm2, engine2) == 0
    assert len(store2.list()) == 1
    assert store2.list()[0].name == "工作"


def test_config_command_invalid_value(cm, engine):
    assert run(_parser_cmd(["config", "redirect_mode", "bogus"]), cm, engine) == 1
    assert run(_parser_cmd(["config", "redirect_mode", "junction"]), cm, engine) == 0
    assert cm.get().redirect_mode == "junction"
