# -*- coding: utf-8 -*-
"""Shell 刷新：SHChangeNotify、WM_SETTINGCHANGE 广播、重启 explorer、视图切换校验。

关键机制（修复“切换后视图未真正切换”问题）：
Windows 桌面视图（explorer 的 SHELLDLL_DefView）缓存“桌面”虚拟文件夹 PIDL，
只修改注册表 + 发送通知**不会**让桌面视图指向新文件夹，必须重启 explorer。
因此 refresh 的 verify 回调应由调用方提供“桌面视图是否显示校验标记文件”的判定；
auto 模式在视图未切换时自动重启 explorer，重启后视图必然指向新路径。
"""
from __future__ import annotations

import ctypes
import subprocess
import time
from typing import Callable, Optional, Tuple

from .constants import (HWND_BROADCAST, SHCNE_ASSOCCHANGED, SHCNF_FLUSHNOWAIT,
                        SHCNF_IDLIST, WM_SETTINGCHANGE)
from .logger_setup import get_logger

log = get_logger("refresher")

_user32 = ctypes.windll.user32
_shell32 = ctypes.windll.shell32


def notify_shell_changed() -> None:
    """通知 Shell 刷新文件类型关联与图标缓存。"""
    try:
        _shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST | SHCNF_FLUSHNOWAIT, None, None)
    except Exception as exc:  # noqa: BLE001
        log.warning("SHChangeNotify 调用失败：%s", exc)
    try:
        _user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0,
                                    ctypes.c_void_p(), 0x0002, 1000, None)
    except Exception as exc:  # noqa: BLE001
        log.warning("WM_SETTINGCHANGE 广播失败：%s", exc)
    log.info("已发送 Shell 变更通知")


def wait_for_desktop(timeout: float = 5.0) -> bool:
    """轮询等待桌面 ListView 出现（explorer 重启后视图就绪的信号）。"""
    from .icon_layout import find_desktop_listview
    deadline = time.monotonic() + max(timeout, 0.5)
    while time.monotonic() < deadline:
        if find_desktop_listview():
            return True
        time.sleep(0.3)
    return False


def restart_explorer(wait_timeout: float = 8.0) -> bool:
    """重启资源管理器（最可靠的刷新手段）。返回是否成功。"""
    log.warning("正在重启 explorer.exe（任务栏/文件管理器窗口将短暂消失）")
    try:
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"],
                       capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("关闭 explorer 失败：%s", exc)
        return False
    try:
        subprocess.Popen(["explorer.exe"])
    except OSError as exc:
        log.error("启动 explorer 失败：%s", exc)
        return False
    ready = wait_for_desktop(wait_timeout)
    if not ready:
        log.warning("explorer 重启后桌面视图未在 %.1f 秒内就绪", wait_timeout)
    return True


def refresh(mode: str = "auto",
            verify: Optional[Callable[[], bool]] = None,
            timeout: float = 3.0,
            restart_allowed: bool = True) -> Tuple[bool, bool]:
    """按配置模式刷新桌面内容，返回 (是否已生效, 是否重启了 explorer)。

    mode:
      none     - 不刷新
      notify   - 仅发送 Shell 变更通知 + 等待 verify
      restart  - 直接重启 explorer（最可靠）
      auto     - 先通知 + verify；视图未切换则自动重启 explorer
    verify: 可选校验回调，返回 True 表示桌面视图已指向目标文件夹。
            由调用方提供“视图标记文件可见性”判定；None 时视为恒通过。
    restart_allowed: 是否允许自动重启 explorer（测试模式传 False，防止误杀进程）。
    """
    if mode == "none":
        return False, False
    notify_shell_changed()
    if mode == "notify":
        return _wait_verify(verify, timeout), False
    if mode == "restart":
        ok = restart_explorer()
        return ok, True
    # auto：通知 → 校验 → 未切换则重启 explorer
    if _wait_verify(verify, timeout):
        return True, False
    log.info("通知后桌面视图未在 %.1f 秒内切换，转为重启 explorer", timeout)
    if not restart_allowed:
        log.warning("当前不允许重启 explorer（测试/受限模式），视图可能未切换")
        return False, False
    ok = restart_explorer()
    # 重启后桌面视图必然重新枚举（注册表已指向目标），视为已生效
    return ok, True


def _wait_verify(verify: Optional[Callable[[], bool]], timeout: float) -> bool:
    if verify is None:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if verify():
            return True
        time.sleep(0.3)
    return False
