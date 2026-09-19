# -*- coding: utf-8 -*-
"""逻辑桌面管理：CRUD、排序、当前/默认桌面维护。"""
from __future__ import annotations

import os
import re
import shutil
import stat
import time
import uuid
from typing import List, Optional

from .config import AppConfig, ConfigManager, DesktopConfig, expand_path
from .constants import APPDATA_DIR_NAME, DESKTOPS_SUBDIR, DEFAULT_ROOT_ENV
from .logger_setup import get_logger

log = get_logger("store")

_INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')
_MAX_NAME_LEN = 64


def _force_remove_tree(path: str) -> None:
    """Windows 健壮删除目录树：去除只读/系统位并重试。

    桌面目录常带 ReadOnly 属性（配合 desktop.ini），shutil.rmtree 不会自动
    清除只读位，删除目录节点时会抛 WinError 5 (Access denied)。这里在删除过程
    中对每个失败节点 chmod 为可写后重试，并整体重试数次以应对 explorer 短暂占用。
    """
    def _onexc(func, p, exc):  # noqa: ANN001
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except OSError:
            pass

    last = None
    for _ in range(3):
        try:
            shutil.rmtree(path, onexc=_onexc)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.6)
    raise last


class DesktopError(Exception):
    """桌面管理错误基类。"""


class InvalidNameError(DesktopError):
    pass


class DesktopExistsError(DesktopError):
    pass


class DesktopNotFoundError(DesktopError):
    pass


class LastDesktopError(DesktopError):
    pass


class DesktopManager:
    """基于 ConfigManager 的逻辑桌面增删改查。

    desktop_root：逻辑桌面目录的绝对路径根（默认 %USERPROFILE%\\TrueDesk\\Desktops）。
    测试时可注入临时目录，避免污染真实用户目录。
    """

    def __init__(self, cm: ConfigManager, desktop_root: Optional[str] = None):
        self.cm = cm
        if desktop_root is None:
            standard_data = os.path.join(os.path.expandvars("%APPDATA%"), APPDATA_DIR_NAME)
            if os.path.normcase(cm.data_root or "") == os.path.normcase(standard_data):
                # 标准数据根（%APPDATA%\TrueDesk）：FR-02 默认桌面位置，
                # 保留环境变量写法便于跨机器迁移
                self.desktop_root_raw = os.path.join(DEFAULT_ROOT_ENV, DESKTOPS_SUBDIR)
            else:
                # 非标准数据根（--test-mode / --data-root 便携场景）：
                # 桌面文件夹与数据根隔离，避免污染真实用户目录
                self.desktop_root_raw = os.path.join(cm.data_root, DESKTOPS_SUBDIR)
            self.desktop_root = expand_path(self.desktop_root_raw)
        else:
            self.desktop_root_raw = desktop_root
            self.desktop_root = desktop_root

    # ---------- 查询 ----------
    def list(self) -> List[DesktopConfig]:
        cfg = self.cm.get()
        return sorted(cfg.desktops, key=lambda d: (d.sort_order, d.name))

    def get(self, key: str) -> DesktopConfig:
        d = self._find(key)
        if d is None:
            raise DesktopNotFoundError("逻辑桌面不存在：%s" % key)
        return d

    def _find(self, key: str) -> Optional[DesktopConfig]:
        cfg = self.cm.get()
        for d in cfg.desktops:
            if d.id == key or d.name == key:
                return d
        return None

    def current(self) -> Optional[DesktopConfig]:
        cfg = self.cm.get()
        if not cfg.current:
            return None
        return self._find(cfg.current)

    def default(self) -> Optional[DesktopConfig]:
        cfg = self.cm.get()
        if cfg.default:
            return self._find(cfg.default)
        return None

    # ---------- 校验 ----------
    @staticmethod
    def validate_name(name: str) -> str:
        name = (name or "").strip()
        if not name:
            raise InvalidNameError("桌面名称不能为空")
        if len(name) > _MAX_NAME_LEN:
            raise InvalidNameError("桌面名称不能超过 %d 个字符" % _MAX_NAME_LEN)
        if _INVALID_NAME_CHARS.search(name):
            raise InvalidNameError("桌面名称不能包含 \\ / : * ? \" < > | 等字符")
        if name in (".", ".."):
            raise InvalidNameError("桌面名称非法")
        return name

    def _ensure_unique_name(self, name: str, exclude_id: Optional[str] = None) -> None:
        cfg = self.cm.get()
        for d in cfg.desktops:
            if d.name == name and d.id != exclude_id:
                raise DesktopExistsError("已存在同名逻辑桌面：%s" % name)

    def default_desktop_path(self, name: str) -> str:
        """默认路径：<桌面根目录>\\<名称>（默认根目录保留环境变量写法）。"""
        return os.path.join(self.desktop_root_raw, name)

    # ---------- 写操作 ----------
    def create(self, name: str, path: Optional[str] = None) -> DesktopConfig:
        name = self.validate_name(name)
        target = expand_path(path) if path else expand_path(self.default_desktop_path(name))
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            raise DesktopError("创建桌面文件夹失败：%s（%s）" % (target, exc))

        raw_path = path or self.default_desktop_path(name)
        holder = {}

        def _fn(cfg: AppConfig) -> None:
            # 基于磁盘最新配置判重、取排序号，避免过期缓存覆盖其他进程的修改
            for d in cfg.desktops:
                if d.name == name:
                    raise DesktopExistsError("已存在同名逻辑桌面：%s" % name)
            order = max((d.sort_order for d in cfg.desktops), default=0) + 1
            desk = DesktopConfig(id=uuid.uuid4().hex[:12], name=name,
                                 path=raw_path, sort_order=order)
            cfg.desktops.append(desk)
            if not cfg.current:
                cfg.current = desk.id
            if not cfg.default:
                cfg.default = desk.id
            holder["desk"] = desk

        self.cm.mutate(_fn)
        desk = holder["desk"]
        # 公共桌面隔离模式下为新桌面自动建立独立公共文件夹（复制现有公共图标）
        try:
            if self.cm.get().public_redirect_mode == "isolated":
                from .isolation import ensure_public_dir
                ensure_public_dir(self.cm.data_root, desk.id)
        except Exception as exc:  # noqa: BLE001
            log.warning("创建公共文件夹失败（不影响桌面创建）：%s", exc)
        log.info("创建逻辑桌面：%s（%s）", name, desk.id)
        return desk

    def rename(self, key: str, new_name: str, migrate: bool = False) -> DesktopConfig:
        new_name = self.validate_name(new_name)
        desk = self.get(key)
        if desk.name == new_name and not migrate:
            return desk
        did = desk.id

        old_path = desk.expanded_path()
        new_raw_path = desk.path
        if migrate:
            new_path = expand_path(self.default_desktop_path(new_name))
            if os.path.abspath(old_path) == os.path.abspath(new_path):
                migrate = False
            elif os.path.exists(new_path):
                raise DesktopError("目标路径已存在：%s" % new_path)
            else:
                try:
                    os.makedirs(os.path.dirname(new_path), exist_ok=True)
                    os.rename(old_path, new_path)
                    new_raw_path = self.default_desktop_path(new_name)
                    log.info("重命名桌面并迁移目录：%s -> %s", old_path, new_path)
                except OSError as exc:
                    raise DesktopError("迁移桌面文件夹失败：%s（%s）" % (old_path, exc))

        def _fn(cfg: AppConfig) -> None:
            # 在磁盘最新配置中重新判重并定位目标
            for d in cfg.desktops:
                if d.name == new_name and d.id != did:
                    raise DesktopExistsError("已存在同名逻辑桌面：%s" % new_name)
            target = next((d for d in cfg.desktops if d.id == did), None)
            if target is None:
                raise DesktopNotFoundError("逻辑桌面不存在：%s" % key)
            target.name = new_name
            if migrate:
                target.path = new_raw_path

        self.cm.mutate(_fn)
        log.info("重命名逻辑桌面：%s -> %s", desk.name, new_name)
        return self.get(did)

    def delete(self, key: str, delete_folder: bool = False) -> dict:
        """删除逻辑桌面（幂等）。

        返回 {"deleted", "already_gone", "name", "folder_warning"}。
        - 桌面在磁盘配置中已不存在时视为删除成功（already_gone=True），
          不再报错，便于 GUI/CLI 在多进程下安全重试并刷新界面；
        - 配置删除与文件夹删除分离：文件夹删除失败只告警，不影响配置删除结果。
        """
        cfg = self.cm.get()  # mtime 自动失效，拿到磁盘最新配置
        target = next((d for d in cfg.desktops if d.id == key or d.name == key), None)
        if target is None:
            log.info("逻辑桌面已不存在，视为已删除：%s", key)
            return {"deleted": False, "already_gone": True, "name": key,
                    "folder_warning": None}
        if len(cfg.desktops) <= 1:
            raise LastDesktopError("至少保留一个逻辑桌面，无法删除最后一个")

        did = target.id
        dname = target.name
        folder = target.expanded_path()

        def _fn(c: AppConfig) -> None:
            if len(c.desktops) <= 1:
                raise LastDesktopError("至少保留一个逻辑桌面，无法删除最后一个")
            if not any(d.id == did for d in c.desktops):
                return  # 已被其他进程删除
            c.desktops = [d for d in c.desktops if d.id != did]
            if c.current == did:
                nxt = self._fallback_desktop(c)
                c.current = nxt.id if nxt else None
            if c.default == did:
                nxt = self._fallback_desktop(c)
                c.default = nxt.id if nxt else None

        self.cm.mutate(_fn)
        log.info("删除逻辑桌面：%s（保留文件夹=%s）", dname, not delete_folder)

        folder_warning = None
        if delete_folder:
            try:
                if os.path.isdir(folder) and not os.path.islink(folder):
                    _force_remove_tree(folder)
                    log.info("已删除桌面文件夹：%s", folder)
            except OSError as exc:
                # 配置已删除成功；文件夹删除失败不判整体失败，提示用户手动处理
                folder_warning = "文件夹删除失败（配置已删除，文件夹保留）：%s" % exc
                log.warning(folder_warning)
        return {"deleted": True, "already_gone": False, "name": dname,
                "folder_warning": folder_warning}

    def _fallback_desktop(self, cfg: AppConfig) -> Optional[DesktopConfig]:
        if cfg.desktops:
            return sorted(cfg.desktops, key=lambda d: (d.sort_order, d.name))[0]
        return None

    def set_default(self, key: str) -> DesktopConfig:
        desk = self.get(key)

        def _fn(cfg):
            cfg.default = desk.id

        self.cm.mutate(_fn)
        log.info("设置默认桌面：%s", desk.name)
        return desk

    def set_current(self, key: str) -> DesktopConfig:
        desk = self.get(key)

        def _fn(cfg):
            cfg.current = desk.id

        self.cm.mutate(_fn)
        log.info("记录当前桌面：%s", desk.name)
        return desk
