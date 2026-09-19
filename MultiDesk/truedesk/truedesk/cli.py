# -*- coding: utf-8 -*-
"""命令行接口：truedesk switch / list / create / ...（FR-10）。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .config import ConfigManager, DesktopConfig
from .desktop_store import DesktopError, DesktopManager
from .logger_setup import get_logger
from .redirect import RedirectEngine, SwitchError
from .single_instance import OperationLock

log = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="truedesk", description="TrueDesk 真·多桌面命令行")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("list", help="列出全部逻辑桌面")
    p_switch = sub.add_parser("switch", help="切换到指定逻辑桌面")
    p_switch.add_argument("name", help="桌面名称或 id")

    p_create = sub.add_parser("create", help="创建逻辑桌面")
    p_create.add_argument("name", help="桌面名称")
    p_create.add_argument("--path", help="自定义桌面路径（默认 %%USERPROFILE%%\\TrueDesk\\Desktops\\<名称>）")

    p_rename = sub.add_parser("rename", help="重命名逻辑桌面")
    p_rename.add_argument("name", help="当前名称或 id")
    p_rename.add_argument("new_name", help="新名称")
    p_rename.add_argument("--migrate", action="store_true", help="同时迁移文件夹")

    p_delete = sub.add_parser("delete", help="删除逻辑桌面")
    p_delete.add_argument("name", help="桌面名称或 id")
    p_delete.add_argument("--delete-folder", action="store_true", help="同时删除文件夹（默认不删除）")

    p_default = sub.add_parser("set-default", help="设置默认桌面")
    p_default.add_argument("name", help="桌面名称或 id")

    sub.add_parser("restore", help="恢复系统默认桌面（注册表备份值）")

    p_export = sub.add_parser("export", help="导出逻辑桌面配置")
    p_export.add_argument("file", help="导出文件路径")

    p_import = sub.add_parser("import", help="导入逻辑桌面配置（追加，重名跳过）")
    p_import.add_argument("file", help="导入文件路径")

    p_wall = sub.add_parser("wallpaper", help="设置逻辑桌面壁纸")
    p_wall.add_argument("name", help="桌面名称或 id")
    p_wall.add_argument("path", nargs="?", help="壁纸图片路径；省略则清除独立壁纸")

    p_cfg = sub.add_parser("config", help="查看/修改全局配置")
    p_cfg.add_argument("key", nargs="?", help="配置项：redirect_mode / explorer_refresh / hotkey_prefix / restore_on_exit")
    p_cfg.add_argument("value", nargs="?", help="配置值")

    p_auto = sub.add_parser("autostart", help="开机自启管理")
    p_auto.add_argument("state", nargs="?", choices=["on", "off", "status"], default="status")

    p_isolate = sub.add_parser("isolate", help="公共桌面彻底隔离管理（HKLM 重定向，需管理员）")
    p_isolate.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    p_isolate.add_argument("--elevated", action="store_true",
                           help="内部参数：已以管理员身份运行（UAC 提权子进程）")

    sub.add_parser("apply-public", help="内部命令：应用公共桌面待定请求（由提权计划任务调用）")

    sub.add_parser("status", help="显示当前状态")
    return p


def _desktop_line(idx: int, d: DesktopConfig, is_current: bool, is_default: bool) -> str:
    mark = "当前" if is_current else ""
    dft = "默认" if is_default else ""
    flags = " / ".join(x for x in (mark, dft) if x)
    suffix = ("（%s）" % flags) if flags else ""
    return "%2d  %s%s\n     路径：%s" % (idx, d.name, suffix, d.expanded_path())


def run(args: argparse.Namespace, cm: ConfigManager, engine: RedirectEngine) -> int:
    store = DesktopManager(cm)

    if args.command == "list":
        cur = store.current()
        default = store.default()
        desks = store.list()
        if not desks:
            print("（没有任何逻辑桌面）")
            return 0
        for i, d in enumerate(desks, 1):
            print(_desktop_line(i, d, cur is not None and d.id == cur.id,
                                default is not None and d.id == default.id))
        return 0

    if args.command == "status":
        _print_status(cm, store)
        return 0

    if args.command == "autostart":
        from . import autostart
        if args.state == "on":
            autostart.enable()
            print("已启用开机自启")
        elif args.state == "off":
            autostart.disable()
            print("已禁用开机自启")
        else:
            print("开机自启：%s" % ("已启用" if autostart.is_enabled() else "未启用"))
        return 0

    if args.command == "config":
        return _config_cmd(args, cm)

    if args.command == "apply-public":
        # 内部命令：由提权计划任务以管理员身份调用
        from .isolation import apply_pending
        try:
            result = apply_pending(cm.data_root)
            print(result or "（无待应用请求）")
            return 0
        except Exception as exc:  # noqa: BLE001
            print("apply-public 失败：%s" % exc, file=sys.stderr)
            log.error("apply-public 失败：%s", exc)
            return 1

    if args.command == "isolate":
        return _isolate_cmd(args, cm, engine)

    try:
        if args.command == "create":
            with OperationLock():
                d = store.create(args.name, path=args.path)
            print("已创建逻辑桌面：%s（%s）" % (d.name, d.expanded_path()))
            return 0

        if args.command == "switch":
            with OperationLock():
                result = engine.switch(args.name)
            print("已切换到逻辑桌面：%s（刷新=%s）" % (result.desktop.name, result.refreshed))
            return 0

        if args.command == "rename":
            with OperationLock():
                store.rename(args.name, args.new_name, migrate=args.migrate)
            print("已重命名为：%s" % args.new_name)
            return 0

        if args.command == "delete":
            with OperationLock():
                result = store.delete(args.name, delete_folder=args.delete_folder)
            if result.get("already_gone"):
                print("逻辑桌面不存在或已被删除：%s" % args.name)
            else:
                tail = "（文件夹已删除）" if args.delete_folder else "（文件夹保留）"
                print("已删除逻辑桌面：%s%s" % (result.get("name", args.name), tail))
            if result.get("folder_warning"):
                print("警告：%s" % result["folder_warning"])
            return 0

        if args.command == "set-default":
            with OperationLock():
                store.set_default(args.name)
            print("已将“%s”设为默认桌面" % args.name)
            return 0

        if args.command == "restore":
            with OperationLock():
                from .registry_backup import RegistryBackup
                from . import refresher
                RegistryBackup(cm.data_root).restore_default()
                # 公共桌面隔离开启时同步还原系统公共桌面
                from .isolation import PublicIsolation
                iso = PublicIsolation(cm, test_mode=engine.test_mode)
                if iso.is_isolated():
                    try:
                        iso.restore_public()
                        def _clear(cfg):
                            cfg.public_redirect_mode = "none"
                        cm.mutate(_clear)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("还原公共桌面失败（请稍后 truedesk isolate off 重试）：%s", exc)
                refresher.refresh(cm.get().explorer_refresh)
            print("已恢复系统默认桌面")
            return 0

        if args.command == "wallpaper":
            with OperationLock():
                d = store.get(args.name)
                from . import wallpaper as wp
                if args.path:
                    real = wp.validate_wallpaper_file(args.path)
                    def _fn(cfg, did=d.id, real=real):
                        for x in cfg.desktops:
                            if x.id == did:
                                x.wallpaper = real
                                x.wallpaper_enabled = True
                    cm.mutate(_fn)
                    print("已为“%s”设置壁纸：%s" % (d.name, real))
                else:
                    def _fn2(cfg, did=d.id):
                        for x in cfg.desktops:
                            if x.id == did:
                                x.wallpaper = None
                                x.wallpaper_enabled = False
                    cm.mutate(_fn2)
                    print("已清除“%s”的独立壁纸" % d.name)
            return 0

        if args.command == "export":
            _export_cmd(args.file, cm)
            return 0

        if args.command == "import":
            _import_cmd(args.file, cm)
            return 0

    except (DesktopError, SwitchError, OSError) as exc:
        print("操作失败：%s" % exc, file=sys.stderr)
        return 1

    print("未知命令：%s（可用：list / switch / create / rename / delete / set-default / restore / export / import / wallpaper / config / autostart / status）" % args.command)
    return 2


# ---------- 子命令实现 ----------
def _isolate_cmd(args, cm, engine) -> int:
    from . import refresher
    from .isolation import PublicIsolation, is_admin

    iso = PublicIsolation(cm, test_mode=engine.test_mode)
    if args.action == "status":
        print("公共桌面隔离：%s" % iso.status())
        print("当前进程：%s" % ("管理员" if is_admin() else "普通权限（切换时经计划任务提权）"))
        return 0
    try:
        if args.action == "on":
            if not args.elevated and not engine.test_mode and not is_admin():
                # 需要 UAC：提权重跑本命令完成初始化（含创建计划任务）
                print(iso.enable(elevated=False))
                print("提权子进程完成初始化后生效；也可直接以管理员身份运行 truedesk isolate on。")
                return 0
            msg = iso.enable(elevated=args.elevated or engine.test_mode)
            print(msg)
            if not engine.test_mode:
                try:
                    # HKLM 公共桌面路径变更后 explorer 视图需重启才生效
                    refresher.refresh("restart")
                except Exception as exc:  # noqa: BLE001
                    log.warning("刷新桌面失败：%s", exc)
            return 0
        if args.action == "off":
            if not args.elevated and not engine.test_mode and not is_admin():
                print(iso.disable())
                return 0
            msg = iso.disable()
            print(msg)
            if not engine.test_mode:
                try:
                    refresher.refresh("restart")
                except Exception as exc:  # noqa: BLE001
                    log.warning("刷新桌面失败：%s", exc)
            return 0
    except Exception as exc:  # noqa: BLE001
        print("操作失败：%s" % exc, file=sys.stderr)
        log.error("isolate 操作失败：%s", exc)
        return 1
    print("用法：truedesk isolate on|off|status", file=sys.stderr)
    return 2


def _print_status(cm: ConfigManager, store: DesktopManager) -> None:
    from . import autostart
    from .registry_backup import read_desktop_paths
    from .redirect import build_backend
    cfg = cm.get()
    cur = store.current()
    default = store.default()
    print("TrueDesk 状态")
    print("当前桌面    ：%s" % (cur.name if cur else "（无）"))
    print("默认桌面    ：%s" % (default.name if default else "（无）"))
    print("重定向模式  ：%s" % cfg.redirect_mode)
    print("刷新策略    ：%s" % cfg.explorer_refresh)
    from .isolation import PublicIsolation
    print("公共桌面隔离：%s" % PublicIsolation(cm).status())
    print("开机自启    ：%s" % ("已启用" if autostart.is_enabled() else "未启用"))
    try:
        paths = read_desktop_paths()
        target = paths.get("shell_folders") or paths.get("user_shell_folders") or ""
        print("系统桌面指向：%s" % os.path.expandvars(target))
        be = build_backend(cfg.redirect_mode)
        cur_target = be.current_target()
        if cur_target:
            print("后端解析目标：%s" % cur_target)
    except Exception as exc:  # noqa: BLE001
        print("系统桌面指向：读取失败（%s）" % exc)


def _config_cmd(args: argparse.Namespace, cm: ConfigManager) -> int:
    cfg = cm.get()
    if not args.key:
        print("redirect_mode   = %s（registry | junction）" % cfg.redirect_mode)
        print("explorer_refresh= %s（auto | notify | restart | none）" % cfg.explorer_refresh)
        print("hotkey_prefix   = %s" % cfg.hotkey_prefix)
        print("restore_on_exit = %s" % cfg.restore_on_exit)
        return 0
    key = args.key
    if args.value is None:
        print("%s = %s" % (key, getattr(cfg, key, "（未知配置项）")))
        return 0
    value = args.value
    valid = {
        "redirect_mode": ("registry", "junction"),
        "explorer_refresh": ("auto", "notify", "restart", "none"),
        "hotkey_prefix": None,
        "restore_on_exit": ("true", "false"),
    }
    if key not in valid:
        print("未知配置项：%s" % key, file=sys.stderr)
        return 1
    if valid[key] and value not in valid[key]:
        print("配置值非法：%s（可选：%s）" % (value, " / ".join(valid[key])), file=sys.stderr)
        return 1

    def _fn(cfg_, k=key, v=value):
        if k == "restore_on_exit":
            setattr(cfg_, k, v.lower() == "true")
        else:
            setattr(cfg_, k, v)

    with OperationLock():
        cm.mutate(_fn)
    print("已设置 %s = %s" % (key, value))
    return 0


def _export_cmd(path: str, cm: ConfigManager) -> None:
    cfg = cm.get()
    payload = {
        "app": "TrueDesk",
        "version": cfg.version,
        "exported_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "desktops": [d.to_dict() for d in cfg.desktops],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("已导出 %d 个逻辑桌面配置到：%s" % (len(payload["desktops"]), path))


def _import_cmd(path: str, cm: ConfigManager) -> None:
    if not os.path.isfile(path):
        raise DesktopError("导入文件不存在：%s" % path)
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    raw_desktops = payload.get("desktops") or []
    store = DesktopManager(cm)
    imported, skipped = 0, []
    for raw in raw_desktops:
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        try:
            store.get(name)
            skipped.append(name)
            continue
        except DesktopError:
            pass
        d = store.create(name, path=raw.get("path"))
        with OperationLock():
            def _fn(cfg, did=d.id, raw=raw):
                for x in cfg.desktops:
                    if x.id == did:
                        x.wallpaper = raw.get("wallpaper")
                        x.wallpaper_enabled = bool(raw.get("wallpaper_enabled", False))
                        x.icon_layout = dict(raw.get("icon_layout") or {})
                        x.show_icons = bool(raw.get("show_icons", True))
            cm.mutate(_fn)
        imported += 1
    print("导入完成：新增 %d 个，跳过重名 %d 个%s"
          % (imported, len(skipped), ("（%s）" % "、".join(skipped)) if skipped else ""))
