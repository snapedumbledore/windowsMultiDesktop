# -*- coding: utf-8 -*-
"""公共桌面彻底隔离（FR-变更）：每个逻辑桌面独立公共桌面文件夹。

原理：桌面视图 = 用户桌面（HKCU 可重定向） + 公共桌面（%PUBLIC%\\Desktop，
由 HKLM\\...\\Explorer\\User Shell Folders 与 Shell Folders 的 Common Desktop
键定义）+ 系统图标。公共桌面为机器级共享，普通逻辑桌面切换无法隔离。

本模块将 HKLM 的 Common Desktop 两键重定向到 <data_root>\\Public\\<桌面id>，
每个逻辑桌面拥有独立的公共图标副本，切换逻辑桌面时同步切换。HKLM 写操作
需要管理员权限：以管理员运行时可直写；非管理员常驻进程通过计划任务
（TrueDeskApplyPublic，/RL HIGHEST /IT）触发提权子进程完成写操作，
UAC 仅在首次启用隔离时弹窗一次。

约束与安全：
- 仅改 HKLM 的 Explorer 两键 Common Desktop 值（不触碰其他 HKLM 项）；
- 首次启用为每个桌面复制公共桌面现有图标（不移动/不删除源文件）；
- 原始公共桌面路径备份于 <data_root>\\backup\\common_desktop_original.json，
  关闭隔离 / 恢复默认时可一键还原；
- 影响范围：本机所有用户共享的公共桌面路径随之变化（单用户家用机无感，
  多用户机器请知悉）。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import winreg
from typing import Dict, Optional

from .config import ConfigManager
from .constants import (COMMON_DESKTOP_NAME, COMMON_ORIGINAL_FILE, HKLM_SHELL_FOLDERS,
                        HKLM_USER_SHELL_FOLDERS, PENDING_PUBLIC_FILE, SCHEDULER_TASK_NAME)
from .logger_setup import get_logger

log = get_logger("isolation")

# 系统公共桌面源（复制内容用，不删除）
PUBLIC_DESKTOP_SOURCE = os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop")

# test_mode 下模拟的 HKLM 公共桌面值（进程内，不触碰真实注册表）
_FAKE_COMMON: Optional[Dict[str, str]] = None


class IsolationError(Exception):
    """公共桌面隔离操作错误。"""


# ================= 路径与基础读写 =================
def public_dir(data_root: str, desk_id: str) -> str:
    """逻辑桌面的独立公共桌面文件夹。"""
    return os.path.join(data_root, "Public", desk_id)


def default_common_values() -> Dict[str, str]:
    """系统默认公共桌面路径（%PUBLIC%\\Desktop 两键值）。"""
    user_value = "%PUBLIC%\\Desktop"
    shell_value = os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop")
    return {"user_shell_folders_common": user_value, "shell_folders_common": shell_value}


def is_admin() -> bool:
    """当前进程是否以管理员令牌运行。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def read_common_desktop() -> Dict[str, str]:
    """读取 HKLM 两键的 Common Desktop 值。"""
    if _FAKE_COMMON is not None:
        return dict(_FAKE_COMMON)
    result = {}
    for hive_key, name in (
        (HKLM_USER_SHELL_FOLDERS, "user_shell_folders_common"),
        (HKLM_SHELL_FOLDERS, "shell_folders_common"),
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_key, 0,
                                winreg.KEY_READ) as k:
                value, _ = winreg.QueryValueEx(k, COMMON_DESKTOP_NAME)
                result[name] = value
        except OSError:
            result[name] = ""
    return result


def write_common_desktop(user_value: str, shell_value: str) -> None:
    """写 HKLM 两键 Common Desktop（需要管理员；调用方保证提权环境）。"""
    if _FAKE_COMMON is not None:
        _FAKE_COMMON.update({
            "user_shell_folders_common": user_value,
            "shell_folders_common": shell_value,
        })
        log.info("[fake] 公共桌面路径已重定向：user=%s shell=%s", user_value, shell_value)
        return
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, HKLM_USER_SHELL_FOLDERS, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, COMMON_DESKTOP_NAME, 0, winreg.REG_EXPAND_SZ, user_value)
    except OSError as exc:
        raise IsolationError("写入 HKLM User Shell Folders\\Common Desktop 失败（需要管理员权限）：%s" % exc)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, HKLM_SHELL_FOLDERS, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, COMMON_DESKTOP_NAME, 0, winreg.REG_EXPAND_SZ, shell_value)
    except OSError as exc:
        raise IsolationError("写入 HKLM Shell Folders\\Common Desktop 失败（需要管理员权限）：%s" % exc)
    log.info("公共桌面路径已重定向：user=%s shell=%s", user_value, shell_value)


def reset_fake_for_tests() -> None:
    """测试辅助：将 fake HKLM 状态重置为系统默认值（此后所有读写均走 fake）。"""
    global _FAKE_COMMON
    _FAKE_COMMON = {"user_shell_folders_common": "%PUBLIC%\\Desktop",
                    "shell_folders_common": os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop")}


# ================= 原值备份 / 还原 =================
def _original_backup_path(data_root: str) -> str:
    return os.path.join(data_root, "backup", COMMON_ORIGINAL_FILE)


def save_original(data_root: str) -> Dict[str, str]:
    """首次记录 HKLM 公共桌面原值（已存在则跳过），返回原值。"""
    path = _original_backup_path(data_root)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    values = read_common_desktop()
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user_shell_folders_common": values.get("user_shell_folders_common", ""),
        "shell_folders_common": values.get("shell_folders_common", ""),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info("已备份 HKLM 公共桌面原值：%s", path)
    return payload


def load_original(data_root: str) -> Dict[str, str]:
    path = _original_backup_path(data_root)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default_common_values()


# ================= 独立公共文件夹 =================
def ensure_public_dir(data_root: str, desk_id: str, copy_initial: bool = False) -> str:
    """创建桌面的独立公共桌面文件夹。

    copy_initial=False（默认，新建桌面时）：只建空目录，不复制系统公共图标，
    保证新桌面干净。
    copy_initial=True（首次启用隔离时）：目录为空时从系统公共桌面复制一次图标，
    保证已有桌面的公共图标在隔离后仍可见。
    """
    target = public_dir(data_root, desk_id)
    os.makedirs(target, exist_ok=True)
    if not copy_initial:
        return target
    try:
        if os.path.isdir(PUBLIC_DESKTOP_SOURCE) and not os.listdir(target):
            copied = 0
            for entry in os.listdir(PUBLIC_DESKTOP_SOURCE):
                src = os.path.join(PUBLIC_DESKTOP_SOURCE, entry)
                if os.path.isfile(src) and entry.lower() != "desktop.ini":
                    try:
                        shutil.copy2(src, os.path.join(target, entry))
                        copied += 1
                    except OSError as exc:
                        log.warning("复制公共图标失败（跳过）：%s（%s）", entry, exc)
            log.info("桌面 %s 的公共文件夹已初始化：复制 %d 个图标", desk_id, copied)
    except OSError as exc:
        log.warning("初始化公共文件夹失败：%s", exc)
    return target


def ensure_all_public_dirs(cm: ConfigManager) -> int:
    """为全部逻辑桌面建立独立公共文件夹（首次启用隔离时，复制当前公共图标）。"""
    count = 0
    for d in cm.get().desktops:
        target = public_dir(cm.data_root, d.id)
        if not os.path.isdir(target) or not os.listdir(target):
            ensure_public_dir(cm.data_root, d.id, copy_initial=True)
            count += 1
    return count


# ================= 提权通道（计划任务） =================
def _task_command() -> str:
    """提权子进程命令行。

    - 打包 exe（frozen）：TrueDesk.exe apply-public；
    - 源码运行：__main__.py 含相对导入，不能直接执行，必须
      `cd 工程根 && python -X utf8 -m truedesk apply-public`。
    """
    if getattr(sys, "frozen", False):
        return '"%s" apply-public' % sys.executable
    pkg_dir = os.path.dirname(os.path.abspath(__file__))      # ...\truedesk\truedesk
    proj_root = os.path.dirname(pkg_dir)                       # ...\truedesk
    return 'cmd /c cd /d "%s" && python -X utf8 -m truedesk apply-public' % proj_root


def _run_cmd(argv) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def task_exists() -> bool:
    try:
        r = _run_cmd(["schtasks", "/Query", "/TN", SCHEDULER_TASK_NAME])
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def create_scheduler_task() -> str:
    """创建提权计划任务（需管理员）；任务保持启用（schtasks /Run 无法触发
    已禁用任务）。每日 00:00 自动运行一次仅为空转（无待应用请求立即退出）。"""
    cmd = _task_command()
    r = _run_cmd(["schtasks", "/Create", "/F", "/TN", SCHEDULER_TASK_NAME,
                  "/TR", cmd, "/SC", "DAILY", "/ST", "00:00",
                  "/RL", "HIGHEST", "/IT"])
    if r.returncode != 0:
        raise IsolationError(
            "创建提权计划任务失败：%s（需管理员组成员账户；可尝试以管理员身份运行 truedesk isolate on）"
            % (r.stderr or r.stdout or "").strip())
    log.info("提权计划任务已创建（%s，保持启用）", SCHEDULER_TASK_NAME)
    return "已创建提权计划任务"


def trigger_task(data_root: str) -> None:
    """触发计划任务执行 apply-public，并等待其完成（pending 文件消失）。"""
    if not task_exists():
        raise IsolationError("提权计划任务不存在，请以管理员身份运行一次 truedesk isolate on")
    r = _run_cmd(["schtasks", "/Run", "/TN", SCHEDULER_TASK_NAME])
    if r.returncode != 0:
        raise IsolationError("触发提权计划任务失败：%s" % (r.stderr or r.stdout or "").strip())
    # 等待提权子进程完成（写 HKLM + 删除 pending 文件）
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if not os.path.exists(os.path.join(data_root, PENDING_PUBLIC_FILE)):
            return
        time.sleep(0.3)
    log.warning("提权计划任务 20 秒内未完成，公共桌面可能未更新；下次切换会自动重试")
    raise IsolationError("提权计划任务执行超时，公共桌面可能未更新（下次切换自动重试）")


# ================= pending 协议 =================
def plan_pending(data_root: str, action: str, target: Optional[str] = None) -> None:
    """写待应用请求（apply=切换公共桌面 / restore=还原系统公共桌面）。"""
    payload = {
        "action": action,
        "target": target,
        "data_root": data_root,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = os.path.join(data_root, PENDING_PUBLIC_FILE)
    os.makedirs(data_root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info("已写入公共桌面待应用请求：%s", payload)


def apply_pending(data_root: str, test_mode: bool = False) -> Optional[str]:
    """提权进程入口：读 pending 请求，写/还原 HKLM 公共桌面，删除 pending。

    返回处理结果描述；无 pending 时返回 None。test_mode=True 时使用内存模拟
    注册表（绝不触碰真实 HKLM）。
    """
    if test_mode:
        global _FAKE_COMMON  # noqa: PLW0603
        if _FAKE_COMMON is None:
            _FAKE_COMMON = {"user_shell_folders_common": "%PUBLIC%\\Desktop",
                            "shell_folders_common": os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop")}
    path = os.path.join(data_root, PENDING_PUBLIC_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.error("读取公共桌面待应用请求失败：%s", exc)
        return "读取待应用请求失败"
    action = payload.get("action")
    success = False
    try:
        if action == "restore":
            values = load_original(data_root)
            write_common_desktop(values.get("user_shell_folders_common", "%PUBLIC%\\Desktop"),
                                 values.get("shell_folders_common", ""))
            if not values.get("shell_folders_common"):
                write_common_desktop("%PUBLIC%\\Desktop",
                                     os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop"))
            result = "公共桌面已还原为系统默认"
        elif action == "apply":
            target = payload.get("target")
            if not target or not os.path.isdir(target):
                raise IsolationError("目标公共桌面路径无效：%s" % target)
            save_original(data_root)
            write_common_desktop(os.path.normpath(target), os.path.normpath(target))
            result = "公共桌面已指向：%s" % target
        else:
            raise IsolationError("未知请求动作：%s" % action)
        success = True
        log.info("%s", result)
        return result
    finally:
        if success:
            try:
                os.remove(path)
                log.info("已清除公共桌面待应用请求")
            except OSError:
                pass
        else:
            log.warning("公共桌面请求处理失败，保留待应用请求供重试")


# ================= 高层 API =================
class PublicIsolation:
    """公共桌面隔离开关与切换同步。"""

    def __init__(self, cm: ConfigManager, test_mode: bool = False):
        global _FAKE_COMMON  # noqa: PLW0603
        self.cm = cm
        self.test_mode = test_mode
        self.data_root = cm.data_root
        if test_mode and _FAKE_COMMON is None:
            _FAKE_COMMON = {"user_shell_folders_common": "%PUBLIC%\\Desktop",
                            "shell_folders_common": os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop")}

    # ---- 状态 ----
    def status(self) -> str:
        cfg = self.cm.get()
        mode = cfg.public_redirect_mode
        if mode == "isolated":
            try:
                values = read_common_desktop()
                cur = values.get("shell_folders_common") or values.get("user_shell_folders_common") or ""
                return "已启用（公共桌面指向：%s）" % os.path.expandvars(cur)
            except Exception as exc:  # noqa: BLE001
                return "已启用（读取当前公共桌面路径失败：%s）" % exc
        return "未启用（公共图标在所有桌面共享）"

    def is_isolated(self) -> bool:
        return self.cm.get().public_redirect_mode == "isolated"

    # ---- 启用 / 关闭 ----
    def enable(self, elevated: bool = False) -> str:
        """启用彻底隔离：建公共文件夹副本、备份 HKLM 原值、写 HKLM 指向当前桌面。

        elevated=True：调用方已以管理员运行（--elevated 子进程），直写。
        elevated=False 且非管理员：自动 UAC 提权重跑本命令完成初始化。
        """
        if self.is_isolated():
            # 幂等修复：以管理员重跑时重建任务（保持启用）、同步公共桌面到
            # 当前逻辑桌面、应用遗留 pending
            if not self.test_mode and elevated and is_admin():
                try:
                    create_scheduler_task()
                except Exception as exc:  # noqa: BLE001
                    log.warning("重建提权任务失败：%s", exc)
                try:
                    cfg = self.cm.get()
                    cur_id = cfg.current or cfg.default or (cfg.desktops[0].id if cfg.desktops else None)
                    if cur_id:
                        write_common_desktop(os.path.normpath(public_dir(self.data_root, cur_id)),
                                             os.path.normpath(public_dir(self.data_root, cur_id)))
                except Exception as exc:  # noqa: BLE001
                    log.warning("同步公共桌面到当前桌面失败：%s", exc)
                try:
                    result = apply_pending(self.data_root)
                    if result:
                        log.info("已应用遗留公共桌面请求：%s", result)
                except Exception as exc:  # noqa: BLE001
                    log.warning("应用遗留公共桌面请求失败：%s", exc)
            return self.status()
        if not elevated and not self.test_mode and not is_admin():
            return self._relaunch_elevated()
        if self.test_mode:
            reset_fake_for_tests()
            global _FAKE_COMMON  # noqa: PLW0603
            _FAKE_COMMON = {"user_shell_folders_common": "%PUBLIC%\\Desktop",
                            "shell_folders_common": os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop")}
        # 1) 建公共文件夹副本
        created = ensure_all_public_dirs(self.cm)
        # 2) 备份 HKLM 原值
        save_original(self.data_root)
        # 3) 创建提权计划任务（打包 exe / 管理员环境）；test_mode 跳过
        if not self.test_mode:
            create_scheduler_task()
        # 4) 写 HKLM 指向当前桌面公共文件夹
        cfg = self.cm.get()
        cur_id = cfg.current or cfg.default or (cfg.desktops[0].id if cfg.desktops else None)
        if not cur_id:
            raise IsolationError("当前没有可用逻辑桌面")
        target = public_dir(self.data_root, cur_id)
        write_common_desktop(os.path.normpath(target), os.path.normpath(target))

        def _fn(cfg):
            cfg.public_redirect_mode = "isolated"
        self.cm.mutate(_fn)
        log.info("公共桌面彻底隔离已启用（副本初始化=%d）", created)
        return "已启用公共桌面彻底隔离：每个逻辑桌面现在拥有独立的公共图标（首次已复制现有图标）。"

    def disable(self) -> str:
        """关闭隔离：还原系统公共桌面路径。"""
        if not self.is_isolated():
            return "公共桌面隔离当前未启用"
        if not self.test_mode and not is_admin():
            return self._relaunch_elevated(restore=True)
        # 还原 HKLM
        values = load_original(self.data_root)
        write_common_desktop(values.get("user_shell_folders_common", "%PUBLIC%\\Desktop"),
                             values.get("shell_folders_common", ""))
        if not values.get("shell_folders_common"):
            write_common_desktop("%PUBLIC%\\Desktop",
                                 os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop"))

        def _fn(cfg):
            cfg.public_redirect_mode = "none"
        self.cm.mutate(_fn)
        log.info("公共桌面隔离已关闭，公共桌面路径已还原")
        return "已关闭公共桌面彻底隔离，公共桌面路径已还原为系统默认。"

    def _relaunch_elevated(self, restore: bool = False) -> str:
        """以管理员权限重跑自身完成初始化/还原（UAC 弹窗一次），并等待子进程完成。"""
        workdir = None
        if getattr(sys, "frozen", False):
            argv = [sys.executable, "isolate",
                    "on" if not restore else "off", "--elevated"]
        else:
            pkg_dir = os.path.dirname(os.path.abspath(__file__))
            proj_root = os.path.dirname(pkg_dir)
            argv = [sys.executable, "-m", "truedesk", "isolate",
                    "on" if not restore else "off", "--elevated"]
            workdir = proj_root
        try:
            result = ctypes.windll.shell32.ShellExecuteW(None, "runas", argv[0],
                                                         " ".join('"%s"' % a for a in argv[1:]),
                                                         workdir, 1)
        except Exception as exc:  # noqa: BLE001
            raise IsolationError("请求管理员权限失败：%s" % exc)
        # ShellExecuteW 返回句柄，>32 表示成功；<=32 为错误码（5=拒绝，1223=用户取消）
        if result <= 32:
            raise IsolationError(
                "请求管理员权限未成功（错误码 %d）：可能被用户取消或系统策略阻止；"
                "请手动右键以管理员身份运行 truedesk isolate on。" % result)
        # 等待提权子进程完成（写配置 public_redirect_mode）
        expect = "isolated" if not restore else "none"
        deadline = time.time() + 120.0
        while time.time() < deadline:
            try:
                if self.cm.get().public_redirect_mode == expect:
                    return "已完成（管理员权限子进程执行成功）"
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        return "已在 UAC 弹窗中请求管理员权限；若已点击“是”则操作完成，可用 truedesk isolate status 确认。"

    # ---- 切换同步 ----
    def sync_for_desktop(self, desk_id: str, refresh_mode: Optional[str] = None) -> str:
        """切换逻辑桌面时同步公共桌面指向（HKLM 重定向）。

        - test_mode / 管理员：直接写；
        - 非管理员常驻进程：写入 pending 请求并触发提权计划任务。
        """
        target = public_dir(self.data_root, desk_id)
        ensure_public_dir(self.data_root, desk_id)
        if self.test_mode:
            write_common_desktop(os.path.normpath(target), os.path.normpath(target))
            return "公共桌面已指向：%s" % target
        if is_admin():
            save_original(self.data_root)
            write_common_desktop(os.path.normpath(target), os.path.normpath(target))
            return "公共桌面已指向：%s" % target
        plan_pending(self.data_root, "apply", target)
        try:
            trigger_task(self.data_root)
            return "公共桌面已指向：%s" % target
        except IsolationError as exc:
            # pending 保留，下次切换自动重试；不阻断用户桌面切换
            log.warning("公共桌面同步失败（保留待应用请求，下次切换重试）：%s", exc)
            raise

    def restore_public(self) -> str:
        """还原系统公共桌面（关闭隔离 / 恢复默认时调用）。"""
        if self.test_mode:
            write_common_desktop("%PUBLIC%\\Desktop",
                                 os.path.join(os.path.expandvars("%PUBLIC%"), "Desktop"))
            return "公共桌面已还原"
        if is_admin():
            values = load_original(self.data_root)
            write_common_desktop(values.get("user_shell_folders_common", "%PUBLIC%\\Desktop"),
                                 values.get("shell_folders_common", ""))
            return "公共桌面已还原"
        plan_pending(self.data_root, "restore")
        try:
            trigger_task(self.data_root)
            return "公共桌面已还原"
        except IsolationError as exc:
            log.warning("公共桌面还原失败（保留待应用请求，稍后重试）：%s", exc)
            raise
