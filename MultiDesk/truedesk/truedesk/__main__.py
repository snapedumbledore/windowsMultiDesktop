# -*- coding: utf-8 -*-
"""TrueDesk 程序入口：参数路由（托盘/主界面/命令行）、系统检测、首次运行初始化、启动恢复。"""
from __future__ import annotations

import argparse
import os
import platform
import sys
import tempfile
from typing import Optional, Tuple

from .config import ConfigManager
from .constants import (APP_DISPLAY_NAME, APP_NAME, APP_VERSION, DEFAULT_REFRESH_MODE,
                        MIN_WIN10_BUILD)
from .logger_setup import get_logger, setup_logging

log = get_logger("main")


# ================= 系统检测（FR-32） =================
def is_supported_system() -> Tuple[bool, str]:
    if sys.platform != "win32":
        return False, "TrueDesk 仅支持 Windows 10 1903 及以上版本"
    try:
        version = platform.version()  # 形如 "10.0.19045.3930"
        parts = version.split(".")
        major = int(parts[0])
        build = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return False, "无法识别系统版本：%s" % platform.version()
    if major == 10 and build >= MIN_WIN10_BUILD:
        return True, ""
    return False, "当前系统版本不受支持（Windows 10 1903+ 或 Windows 11；当前 build %d）" % build


# ================= 首次运行初始化（FR-01） =================
def init_first_run(cm: ConfigManager, test_mode: bool = False) -> None:
    """首次运行：备份注册表原值，创建“默认”逻辑桌面（指向当前系统桌面，不移动文件）。"""
    log.info("首次运行初始化")
    from .desktop_store import DesktopManager
    from .registry_backup import RegistryBackup, read_desktop_paths

    backup = RegistryBackup(cm.data_root, test_mode=test_mode)
    try:
        backup.backup()
        log.info("已备份桌面路径注册表原值")
    except Exception as exc:  # noqa: BLE001
        log.warning("首次运行备份注册表失败：%s", exc)

    paths = read_desktop_paths() if not test_mode else {}
    raw = paths.get("shell_folders") or paths.get("user_shell_folders") or r"%USERPROFILE%\Desktop"
    store = DesktopManager(cm)
    try:
        store.create("默认", path=raw)
        log.info("已创建“默认”逻辑桌面，路径：%s", raw)
    except Exception as exc:  # noqa: BLE001
        log.error("创建默认逻辑桌面失败：%s", exc)
        raise


# ================= 参数解析 =================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="truedesk", description="%s v%s" % (APP_DISPLAY_NAME, APP_VERSION))
    p.add_argument("--tray", action="store_true", help="以托盘方式常驻（默认）")
    p.add_argument("--gui", action="store_true", help="打开主界面")
    p.add_argument("--restore", action="store_true", help="恢复上次/默认逻辑桌面后退出")
    p.add_argument("--test-mode", action="store_true",
                   help="测试模式：使用临时数据目录，不修改真实注册表")
    p.add_argument("--data-root", help="自定义数据目录（默认 %%APPDATA%%\\TrueDesk）")
    return p


CLI_COMMANDS = {
    "list", "switch", "create", "rename", "delete", "set-default", "restore",
    "export", "import", "wallpaper", "config", "autostart", "status",
    "isolate", "apply-public",
}


def _first_positional(argv: list) -> Optional[str]:
    for a in argv:
        if a.startswith("-"):
            continue
        return a
    return None


def _extract_global(argv: list):
    """从参数中提取 --test-mode/--data-root，其余（含子命令及 --help）原样保留。"""
    test_mode = False
    data_root: Optional[str] = None
    rest: list = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--test-mode":
            test_mode = True
        elif a == "--data-root":
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                data_root = argv[i + 1]
                i += 1
        elif a.startswith("--data-root="):
            data_root = a.split("=", 1)[1]
        elif a in ("--tray", "--gui", "--restore"):
            pass  # CLI 子命令模式下忽略模式开关
        else:
            rest.append(a)
        i += 1
    return test_mode, data_root, rest


# ================= 入口 =================
def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # CLI 子命令分流（restore 子命令 = 恢复系统默认桌面；全局参数仍生效）
    if _first_positional(argv) in CLI_COMMANDS:
        from .cli import build_parser as build_cli_parser, run as cli_run
        test_mode, data_root, cli_argv = _extract_global(argv)
        if test_mode:
            data_root = data_root or os.path.join(tempfile.gettempdir(), "TrueDeskTest")
            os.makedirs(data_root, exist_ok=True)
        cm = ConfigManager(data_root=data_root)
        setup_logging(cm.data_root)
        ok, msg = is_supported_system()
        if not ok:
            print(msg)
            return 1
        if not os.path.exists(cm.config_path):
            init_first_run(cm, test_mode=test_mode)
        from .redirect import RedirectEngine
        engine = RedirectEngine(cm, test_mode=test_mode)
        cli_args = build_cli_parser().parse_args(cli_argv)
        return cli_run(cli_args, cm, engine)

    args = build_parser().parse_args(argv)

    # 数据目录：test-mode 使用临时目录
    if args.test_mode:
        data_root = args.data_root or os.path.join(tempfile.gettempdir(), "TrueDeskTest")
        os.makedirs(data_root, exist_ok=True)
    else:
        data_root = args.data_root
    cm = ConfigManager(data_root=data_root)
    setup_logging(cm.data_root)

    ok, msg = is_supported_system()
    if not ok:
        print(msg)
        log.error(msg)
        return 1

    # 首次运行初始化
    try:
        if not os.path.exists(cm.config_path):
            init_first_run(cm, test_mode=args.test_mode)
    except Exception as exc:  # noqa: BLE001
        print("初始化失败：%s" % exc)
        return 1

    from .desktop_store import DesktopManager
    from .recovery import RecoveryRunner
    from .redirect import RedirectEngine
    from .single_instance import SingleInstance

    store = DesktopManager(cm)
    engine = RedirectEngine(cm, test_mode=args.test_mode)
    recovery = RecoveryRunner(cm, test_mode=args.test_mode)

    # 恢复未完成切换事务
    try:
        recovery.run()
    except Exception as exc:  # noqa: BLE001
        log.error("启动恢复失败：%s", exc)

    # 仅恢复上次桌面（--restore，开机自启场景）
    if args.restore and not args.gui:
        try:
            engine.ensure_current()
        except Exception as exc:  # noqa: BLE001
            log.error("恢复上次桌面失败：%s", exc)
        return 0

    # 托盘模式与 GUI 模式：单实例
    if args.gui:
        inst = SingleInstance("TrueDesk.SingleInstance")
        if not inst.acquire():
            print("TrueDesk 已在运行（托盘模式），主界面可从托盘打开。")
            return 0
        from .gui import run_gui
        log.info("主界面已打开（GUI 模式）")
        try:
            return run_gui(cm, engine)
        finally:
            inst.release()

    inst = SingleInstance("TrueDesk.SingleInstance")
    if not inst.acquire():
        print("TrueDesk 已在运行。")
        return 0
    try:
        # 托盘 + 快捷键
        from .hotkey import HotkeyManager
        from .tray import TrayApp

        hotkeys = HotkeyManager()

        def _hotkey_switch(index: int) -> None:
            desks = store.list()
            if 0 <= index < len(desks):
                try:
                    from .single_instance import OperationLock
                    with OperationLock():
                        engine.switch(desks[index].id, restore_state=True)
                except Exception as exc:  # noqa: BLE001
                    log.error("快捷键切换失败：%s", exc)
            else:
                log.warning("快捷键对应桌面不存在")

        prefix = cm.get().hotkey_prefix
        for i in range(1, 10):
            hotkeys.register(i, lambda i=i: _hotkey_switch(i - 1), prefix=prefix)

        tray = TrayApp(cm, engine)
        tray.run_detached()
        hotkeys.start()
        log.info("%s v%s 已启动（托盘模式，Ctrl+Alt+1..9 切换）", APP_DISPLAY_NAME, APP_VERSION)
        print("%s v%s 已启动。托盘图标位于通知区域；Ctrl+Alt+1..9 切换前 9 个桌面。" % (APP_DISPLAY_NAME, APP_VERSION))
        try:
            import time
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            hotkeys.stop()
            tray.stop()
        return 0
    finally:
        inst.release()


if __name__ == "__main__":
    sys.exit(main())
