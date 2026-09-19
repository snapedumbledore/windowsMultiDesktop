# -*- coding: utf-8 -*-
"""切换引擎：registry / junction 双后端，切换事务与回滚。"""
from __future__ import annotations

import abc
import os
import subprocess
import time
from typing import Callable, Dict, Optional

from . import refresher, wallpaper
from .config import ConfigManager, DesktopConfig, expand_path
from .constants import (DEFAULT_REFRESH_MODE, PENDING_STEP_BACKED_UP, PENDING_STEP_DONE,
                        PENDING_STEP_REFRESHED, PENDING_STEP_WRITTEN)
from .desktop_store import DesktopManager
from .icon_layout import apply_layout, apply_show_icons, save_layout
from .logger_setup import get_logger
from .registry_backup import RegistryBackup, RegistryError, read_desktop_paths, write_desktop_paths

log = get_logger("redirect")

DEFAULT_DESKTOP_LINK = os.path.join(os.path.expandvars("%USERPROFILE%"), "Desktop")


class SwitchError(Exception):
    """切换失败（含回滚说明）。"""


# ================= 后端 =================
class RedirectBackend(abc.ABC):
    """桌面重定向后端抽象。"""

    name = "base"

    @abc.abstractmethod
    def apply(self, target_path: str) -> None:
        """将系统桌面指向 target_path。"""

    @abc.abstractmethod
    def current_target(self) -> Optional[str]:
        """当前系统桌面指向的路径（解析后）；无法确定返回 None。"""

    @abc.abstractmethod
    def restore(self, snapshot: Dict[str, str]) -> None:
        """用快照恢复原状态。"""

    def verify(self, target_path: str) -> bool:
        cur = self.current_target()
        return bool(cur) and os.path.normcase(os.path.normpath(cur)) == os.path.normcase(
            os.path.normpath(target_path))


class RegistryBackend(RedirectBackend):
    """主后端：修改 HKCU 桌面已知文件夹两个键。"""

    name = "registry"

    def __init__(self, test_mode: bool = False):
        self.test_mode = test_mode
        self._fake: Dict[str, str] = {}

    def _read(self) -> Dict[str, str]:
        if self.test_mode:
            return dict(self._fake)
        return read_desktop_paths()

    def _write(self, user_value: str, shell_value: str) -> None:
        if self.test_mode:
            self._fake = {"user_shell_folders": user_value, "shell_folders": shell_value}
            return
        write_desktop_paths(user_value, shell_value)

    def apply(self, target_path: str) -> None:
        user_value = os.path.normpath(target_path)
        self._write(user_value, user_value)

    def current_target(self) -> Optional[str]:
        paths = self._read()
        value = paths.get("shell_folders") or paths.get("user_shell_folders")
        if not value:
            return None
        return os.path.normpath(os.path.expandvars(value))

    def restore(self, snapshot: Dict[str, str]) -> None:
        user_value = snapshot.get("user_shell_folders") or "%USERPROFILE%\\Desktop"
        shell_value = snapshot.get("shell_folders")
        if not shell_value:
            shell_value = os.path.expandvars(user_value)
        self._write(user_value, shell_value)


class JunctionBackend(RedirectBackend):
    """备选后端：%USERPROFILE%\\Desktop 位置创建目录联接点指向逻辑桌面。"""

    name = "junction"

    def __init__(self, test_mode: bool = False, link_path: Optional[str] = None):
        self.test_mode = test_mode
        self.link_path = link_path or DEFAULT_DESKTOP_LINK
        self._fake_target: Optional[str] = None

    @staticmethod
    def _is_junction(path: str) -> bool:
        try:
            return os.path.islink(path) and os.path.isdir(path)
        except OSError:
            return False

    def _read_target(self) -> Optional[str]:
        if self.test_mode:
            return self._fake_target
        if not self._is_junction(self.link_path):
            return None
        try:
            return os.path.normpath(os.path.realpath(self.link_path))
        except OSError:
            return None

    def _apply_real(self, target_path: str) -> None:
        link = self.link_path
        # 首次切换：原位置是真实目录
        if os.path.isdir(link) and not self._is_junction(link):
            entries = [e for e in os.listdir(link) if not e.startswith(".")]
            if entries:
                raise SwitchError(
                    "原桌面目录 %s 包含 %d 个项目，junction 模式不会自动迁移用户文件。"
                    "请先手动移动桌面内容，或改用注册表模式（truedesk config redirect_mode registry）。"
                    % (link, len(entries)))
            os.rmdir(link)
        elif self._is_junction(link):
            try:
                os.rmdir(link)  # 仅移除联接点本身，不删除目标内容
            except OSError as exc:
                raise SwitchError("移除旧联接点失败：%s（%s）" % (link, exc))
        os.makedirs(os.path.dirname(link), exist_ok=True)
        try:
            result = subprocess.run(["cmd", "/c", "mklink", "/J", link, target_path],
                                    capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SwitchError("创建联接点失败：%s（%s）" % (link, exc))
        if result.returncode != 0:
            raise SwitchError("创建联接点失败：%s（%s）" % (link, result.stderr.strip()))

    def apply(self, target_path: str) -> None:
        if self.test_mode:
            self._fake_target = os.path.normpath(target_path)
            return
        self._apply_real(os.path.normpath(target_path))

    def current_target(self) -> Optional[str]:
        return self._read_target()

    def restore(self, snapshot: Dict[str, str]) -> None:
        target = snapshot.get("junction_target")
        if self.test_mode:
            self._fake_target = target
            return
        if target:
            self._apply_real(target)
        else:
            link = self.link_path
            if self._is_junction(link):
                try:
                    os.rmdir(link)
                except OSError as exc:
                    raise SwitchError("回滚时移除联接点失败：%s（%s）" % (link, exc))


def build_backend(mode: str, test_mode: bool = False) -> RedirectBackend:
    if mode == "junction":
        return JunctionBackend(test_mode=test_mode)
    return RegistryBackend(test_mode=test_mode)


# ================= 切换引擎 =================
class SwitchResult:
    def __init__(self, desktop: DesktopConfig, refreshed: bool, restored_state: int):
        self.desktop = desktop
        self.refreshed = refreshed
        self.restored_state = restored_state  # 恢复的图标数量（best-effort）


class RedirectEngine:
    """切换事务编排：校验→保存状态→备份→写入→刷新→恢复状态→更新配置。"""

    def __init__(self, cm: ConfigManager, test_mode: bool = False,
                 state_delay: float = 2.0):
        self.cm = cm
        self.store = DesktopManager(cm)
        self.backup = RegistryBackup(cm.data_root, test_mode=test_mode)
        self.test_mode = test_mode
        self.state_delay = state_delay  # 应用图标布局前等待 explorer 就绪的秒数
        self._backend = None
        self._backend_mode: Optional[str] = None

    def backend(self) -> RedirectBackend:
        """惰性创建并缓存后端；redirect_mode 变化时重建（保持 test_mode 状态一致）。"""
        mode = self.cm.get().redirect_mode
        if self._backend is None or self._backend_mode != mode:
            self._backend = build_backend(mode, test_mode=self.test_mode)
            self._backend_mode = mode
        return self._backend

    # ---------- 切换 ----------
    def switch(self, key: str, refresh_mode: Optional[str] = None,
               restore_state: bool = True) -> SwitchResult:
        desk = self.store.get(key)
        target = desk.expanded_path()
        if not os.path.isdir(target):
            try:
                os.makedirs(target, exist_ok=True)
            except OSError as exc:
                raise SwitchError("目标桌面路径不可用：%s（%s）" % (target, exc))
        mode = refresh_mode or self.cm.get().explorer_refresh or DEFAULT_REFRESH_MODE
        old = self.store.current()
        log.info("切换开始：%s -> %s（模式=%s）",
                 old.name if old else "（无）", desk.name, mode)

        # 1) 保存当前桌面状态（布局等；失败仅告警）
        self._save_current_state()

        # 2) 备份注册表原值
        try:
            snapshot_path = self.backup.backup()
        except Exception as exc:  # noqa: BLE001
            raise SwitchError("备份注册表原值失败，已中止切换：%s" % exc)
        snapshot = self._snapshot_from_backup(snapshot_path)

        # 3) 写入 pending 事务（步骤=已备份）
        self._set_pending(desk, target, snapshot_path, PENDING_STEP_BACKED_UP)

        # 4) 应用重定向
        be = self.backend()
        try:
            be.apply(target)
        except Exception as exc:  # noqa: BLE001
            self._rollback(snapshot_path, snapshot, be, "应用重定向失败：%s" % exc)
            raise SwitchError("切换失败：%s；已回滚到切换前状态。" % exc)
        self._set_pending(desk, target, snapshot_path, PENDING_STEP_WRITTEN)

        # 4.5) 公共桌面隔离模式：同步 HKLM 公共桌面指向（失败不阻断用户桌面切换）
        if self.cm.get().public_redirect_mode == "isolated":
            try:
                from .isolation import PublicIsolation
                PublicIsolation(self.cm, test_mode=self.test_mode).sync_for_desktop(desk.id)
            except Exception as exc:  # noqa: BLE001
                log.warning("同步公共桌面失败（用户桌面已切换，公共图标可能来自上一桌面）：%s", exc)

        # 5) 刷新并校验桌面视图已真正切换
        refreshed, restarted = self._refresh_view(mode, be, target)
        self._set_pending(desk, target, snapshot_path, PENDING_STEP_REFRESHED)

        # 6) 恢复目标桌面状态（壁纸 / 图标布局 / 显示设置）
        restored = 0
        if restore_state:
            restored = self._restore_target_state(desk, refreshed, restarted)

        # 7) 更新 current 并清空 pending
        def _finish(cfg, did=desk.id):
            cfg.current = did
            cfg.pending_switch = None

        self.cm.mutate(_finish)
        log.info("切换完成：%s -> %s（视图生效=%s，重启=%s，恢复图标=%d）",
                 old.name if old else "（无）", desk.name, refreshed, restarted, restored)
        return SwitchResult(desk, refreshed, restored)

    def _refresh_view(self, mode: str, be: RedirectBackend, target: str):
        """按模式刷新并校验“桌面视图真正指向目标文件夹”。

        - 真实模式：在目标文件夹写入一个可见的临时校验标记，verify = 桌面视图
          是否显示该标记；视图未切换（标记不可见）时 auto/restart 模式自动重启
          explorer。注意不能用隐藏点文件作标记（桌面默认不显示隐藏文件，会导致
          校验恒失败）。
        - 测试模式：verify = 后端注册表值校验（不触碰真实注册表/进程）。
        返回 (refreshed, restarted)。
        """
        import uuid

        marker_path: Optional[str] = None
        verify = lambda: be.verify(target)
        if not self.test_mode:
            from .icon_layout import view_contains
            marker_name = "TDViewCheck_%s.tmp" % uuid.uuid4().hex[:6]
            marker_path = os.path.join(target, marker_name)
            try:
                with open(marker_path, "w", encoding="utf-8") as f:
                    f.write("truedesk view check\n")
            except OSError as exc:
                log.warning("创建视图校验标记失败（将退化为注册表校验）：%s", exc)
                marker_path = None
            if marker_path:
                verify = lambda: view_contains(marker_name)
        try:
            refreshed, restarted = refresher.refresh(
                mode, verify=verify, timeout=3.0, restart_allowed=not self.test_mode)
        finally:
            # 无论成功失败都移除校验标记，避免残留在用户桌面
            if marker_path:
                try:
                    os.remove(marker_path)
                    refresher.notify_shell_changed()
                except OSError:
                    pass
        return refreshed, restarted

    def ensure_current(self, refresh_mode: Optional[str] = None) -> Optional[SwitchResult]:
        """确保系统桌面指向 current（或默认）逻辑桌面；不一致时执行切换。"""
        cur = self.store.current()
        if cur is None:
            cur = self.store.default()
        if cur is None:
            desks = self.store.list()
            cur = desks[0] if desks else None
        if cur is None:
            return None
        be = self.backend()
        if be.verify(cur.expanded_path()):
            return None
        log.info("系统桌面未指向当前逻辑桌面，恢复中：%s", cur.name)
        return self.switch(cur.id, refresh_mode=refresh_mode, restore_state=True)

    # ---------- 状态保存 / 恢复 ----------
    def _save_current_state(self) -> None:
        cur = self.store.current()
        if cur is None:
            return
        try:
            layout = save_layout()
            if layout.get("saved"):
                # 坐标合理性校验：若图标整体偏右（超过屏幕宽度 60%），
                # 说明 explorer 重启后 Windows 自动重排了，不要把错乱状态固化
                items = layout.get("items") or []
                if items:
                    import ctypes
                    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
                    right_ratio = sum(1 for it in items if it.get("x", 0) > screen_w * 0.6) / len(items)
                    if right_ratio > 0.8:
                        log.warning("检测到图标整体偏右（%.0f%% 在屏幕右侧），"
                                    "可能是 Windows 自动重排，跳过本次布局保存", right_ratio * 100)
                        return
                def _fn(cfg, cid=cur.id, layout=layout):
                    for d in cfg.desktops:
                        if d.id == cid:
                            # 已有用户手动保存的 pinned 基准布局时，切换时的
                            # 自动保存不得覆盖（Windows 异步重排会污染基准）。
                            if (d.icon_layout or {}).get("pinned"):
                                log.info("桌面“%s”已有手动保存的基准布局，跳过自动保存",
                                         d.name)
                                return
                            d.icon_layout = layout
                self.cm.mutate(_fn)
        except Exception as exc:  # noqa: BLE001
            log.warning("保存当前桌面图标布局失败（不影响切换）：%s", exc)

    def _restore_target_state(self, desk: DesktopConfig, refreshed: bool,
                              restarted: bool = False) -> int:
        restored = 0
        # 壁纸
        try:
            wallpaper.apply_desktop_wallpaper(desk.wallpaper, desk.wallpaper_enabled)
        except Exception as exc:  # noqa: BLE001
            log.warning("应用壁纸失败（不影响切换）：%s", exc)
        # 显示/隐藏图标
        try:
            apply_show_icons(desk.show_icons)
        except Exception as exc:  # noqa: BLE001
            log.warning("应用显示设置失败：%s", exc)
        # 图标布局：explorer 重启后需等待桌面视图就绪
        if desk.icon_layout and desk.icon_layout.get("saved"):
            if restarted:
                refresher.wait_for_desktop(timeout=max(self.state_delay, 3.0))
                time.sleep(0.5)
            elif refreshed and self.state_delay > 0:
                time.sleep(self.state_delay)
            try:
                restored = apply_layout(desk.icon_layout)
            except Exception as exc:  # noqa: BLE001
                log.warning("恢复图标布局失败（不影响切换）：%s", exc)
        return restored

    # ---------- 事务 ----------
    def _set_pending(self, desk: DesktopConfig, target: str,
                     snapshot_path: str, step: str) -> None:
        def _fn(cfg, desk=desk, target=target, snapshot_path=snapshot_path, step=step):
            cfg.pending_switch = {
                "desktop_id": desk.id,
                "desktop_name": desk.name,
                "target_path": target,
                "snapshot_path": snapshot_path,
                "step": step,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        self.cm.mutate(_fn)

    def _snapshot_from_backup(self, snapshot_path: str) -> Dict[str, str]:
        import json
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            return {
                "user_shell_folders": payload.get("user_shell_folders_desktop", ""),
                "shell_folders": payload.get("shell_folders_desktop", ""),
                "junction_target": self.backend().current_target(),
            }
        except (OSError, ValueError):
            return {
                "user_shell_folders": "%USERPROFILE%\\Desktop",
                "shell_folders": os.path.join(os.path.expandvars("%USERPROFILE%"), "Desktop"),
                "junction_target": None,
            }

    def _rollback(self, snapshot_path: str, snapshot: Dict[str, str],
                  backend: RedirectBackend, reason: str) -> None:
        log.error("切换回滚：%s", reason)
        try:
            backend.restore(snapshot)
            log.info("已回滚桌面指向")
        except Exception as exc:  # noqa: BLE001
            log.critical("回滚失败：%s；可手动执行 truedesk restore（%s）", exc, snapshot_path)

        def _clear(cfg):
            cfg.pending_switch = None
        try:
            self.cm.mutate(_clear)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 恢复/回滚入口（供 recovery 使用） ----------
    def complete_or_rollback(self) -> Optional[SwitchResult]:
        """启动时处理未完成的切换事务。"""
        cfg = self.cm.load()
        pending = cfg.pending_switch
        if not pending:
            return None
        desk = self.store.get(pending.get("desktop_id") or pending.get("desktop_name"))
        target = pending.get("target_path") or desk.expanded_path()
        snapshot_path = pending.get("snapshot_path")
        snapshot = self._snapshot_from_backup(snapshot_path) if snapshot_path else {}
        be = self.backend()
        if be.verify(target):
            log.info("检测到未完成的切换：补完（目标 %s）", desk.name)
            self._set_pending(desk, target, snapshot_path or "", PENDING_STEP_WRITTEN)
            refreshed, restarted = self._refresh_view(
                self.cm.get().explorer_refresh or DEFAULT_REFRESH_MODE, be, target)
            self._set_pending(desk, target, snapshot_path or "", PENDING_STEP_REFRESHED)
            restored = self._restore_target_state(desk, refreshed, restarted)

            def _finish(cfg, did=desk.id):
                cfg.current = did
                cfg.pending_switch = None
            self.cm.mutate(_finish)
            log.info("切换事务补完：%s", desk.name)
            return SwitchResult(desk, refreshed, restored)
        # 未指向目标：回滚到切换前状态
        log.warning("检测到未完成的切换但桌面未指向目标，执行回滚")
        try:
            be.restore(snapshot)
        except Exception as exc:  # noqa: BLE001
            log.critical("回滚失败：%s；请手动执行 truedesk restore", exc)
        try:
            self.cm.mutate(lambda c: setattr(c, "pending_switch", None))
        except Exception:  # noqa: BLE001
            pass
        return None
