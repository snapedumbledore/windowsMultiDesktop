# -*- coding: utf-8 -*-
"""配置读写：%APPDATA%\\TrueDesk\\config.json（UTF-8 JSON，路径支持环境变量）。"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .constants import APPDATA_DIR_NAME, CONFIG_FILE, DEFAULT_HOTKEY_PREFIX, DEFAULT_REFRESH_MODE

CONFIG_VERSION = 1


class ConfigError(Exception):
    """配置加载/校验错误。"""


@dataclass
class DesktopConfig:
    """单个逻辑桌面的配置。path 保留原始写法（可含环境变量）。"""

    id: str
    name: str
    path: str
    wallpaper: Optional[str] = None
    wallpaper_enabled: bool = False
    icon_layout: Dict[str, Any] = field(default_factory=dict)
    show_icons: bool = True
    sort_order: int = 0

    def expanded_path(self) -> str:
        return expand_path(self.path)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DesktopConfig":
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex[:8]),
            name=str(d.get("name") or "").strip(),
            path=str(d.get("path") or ""),
            wallpaper=d.get("wallpaper"),
            wallpaper_enabled=bool(d.get("wallpaper_enabled", False)),
            icon_layout=dict(d.get("icon_layout") or {}),
            show_icons=bool(d.get("show_icons", True)),
            sort_order=int(d.get("sort_order", 0)),
        )


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    current: Optional[str] = None          # 当前逻辑桌面 id
    default: Optional[str] = None          # 开机/退出后恢复的桌面 id
    restore_on_exit: bool = True
    redirect_mode: str = "registry"        # registry | junction
    explorer_refresh: str = DEFAULT_REFRESH_MODE  # auto | notify | restart | none
    hotkey_prefix: str = DEFAULT_HOTKEY_PREFIX
    public_redirect_mode: str = "none"     # none | isolated（公共桌面彻底隔离）
    desktops: List[DesktopConfig] = field(default_factory=list)
    pending_switch: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "current": self.current,
            "default": self.default,
            "restore_on_exit": self.restore_on_exit,
            "redirect_mode": self.redirect_mode,
            "explorer_refresh": self.explorer_refresh,
            "hotkey_prefix": self.hotkey_prefix,
            "public_redirect_mode": self.public_redirect_mode,
            "desktops": [d.to_dict() for d in self.desktops],
            "pending_switch": self.pending_switch,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AppConfig":
        cfg = cls(
            version=int(d.get("version", CONFIG_VERSION)),
            current=d.get("current"),
            default=d.get("default"),
            restore_on_exit=bool(d.get("restore_on_exit", True)),
            redirect_mode=str(d.get("redirect_mode", "registry")),
            explorer_refresh=str(d.get("explorer_refresh", DEFAULT_REFRESH_MODE)),
            hotkey_prefix=str(d.get("hotkey_prefix", DEFAULT_HOTKEY_PREFIX)),
            public_redirect_mode=str(d.get("public_redirect_mode", "none")),
            pending_switch=d.get("pending_switch"),
        )
        cfg.desktops = [DesktopConfig.from_dict(x) for x in (d.get("desktops") or [])]
        if cfg.redirect_mode not in ("registry", "junction"):
            cfg.redirect_mode = "registry"
        if cfg.explorer_refresh not in ("auto", "notify", "restart", "none"):
            cfg.explorer_refresh = DEFAULT_REFRESH_MODE
        if cfg.public_redirect_mode not in ("none", "isolated"):
            cfg.public_redirect_mode = "none"
        return cfg


def expand_path(path: str) -> str:
    """展开路径中的环境变量并返回绝对路径。"""
    if not path:
        return ""
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def default_data_root() -> str:
    """%APPDATA%\\TrueDesk"""
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, APPDATA_DIR_NAME)


class ConfigManager:
    """config.json 的加载/保存/校验（线程安全）。"""

    def __init__(self, config_path: Optional[str] = None, data_root: Optional[str] = None):
        self.data_root = data_root or default_data_root()
        self.config_path = config_path or os.path.join(self.data_root, CONFIG_FILE)
        self._lock = threading.RLock()
        self._cfg: Optional[AppConfig] = None
        self._mtime: Optional[int] = None  # 已加载配置对应的文件修改时间

    def _mtime_locked(self) -> Optional[int]:
        try:
            return os.stat(self.config_path).st_mtime_ns
        except OSError:
            return None

    # ---- 路径 ----
    def backup_dir(self) -> str:
        return os.path.join(self.data_root, "backup")

    def ensure_dirs(self) -> None:
        os.makedirs(self.data_root, exist_ok=True)
        os.makedirs(self.backup_dir(), exist_ok=True)

    # ---- 加载 / 保存 ----
    def load(self) -> AppConfig:
        with self._lock:
            if self._cfg is not None:
                # 检测其他进程（提权计划任务 / CLI / 托盘）是否改写过配置；
                # 文件修改时间变化则丢弃内存缓存、重读磁盘最新内容。
                if self._mtime is None or self._mtime_locked() == self._mtime:
                    return self._cfg
            self._cfg = self._load_locked()
            self._mtime = self._mtime_locked()
            return self._cfg

    def _load_locked(self) -> AppConfig:
        if not os.path.exists(self.config_path):
            cfg = AppConfig()
            self._write_locked(cfg)
            return cfg
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise ConfigError("配置文件损坏或不可读：%s（%s）" % (self.config_path, exc))
        if not isinstance(raw, dict):
            raise ConfigError("配置文件结构非法：%s" % self.config_path)
        cfg = AppConfig.from_dict(raw)
        # 校验与修复：保证至少存在一个桌面
        if not cfg.desktops:
            raise ConfigError("配置中不存在任何逻辑桌面，请删除配置文件后重新初始化。")
        return cfg

    def save(self, cfg: Optional[AppConfig] = None) -> None:
        with self._lock:
            if cfg is not None:
                self._cfg = cfg
            self._write_locked(self._cfg)
            self._mtime = self._mtime_locked()

    def _write_locked(self, cfg: AppConfig) -> None:
        self.ensure_dirs()
        tmp = self.config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.config_path)

    def reload(self) -> AppConfig:
        with self._lock:
            self._cfg = None
            return self.load()

    # ---- 便捷访问 ----
    def get(self) -> AppConfig:
        return self.load()

    def mutate(self, fn):
        """原子读改写：fn(cfg) -> None，执行后保存。

        每次 mutate 都强制从磁盘重新加载最新 config，避免内存缓存过期
        导致多进程（GUI + 提权计划任务 + CLI）互相覆盖修改。
        若磁盘 config 损坏或为空，从空配置开始（fn 会填充内容）。
        """
        with self._lock:
            self._cfg = None  # 强制重新加载磁盘最新 config
            try:
                cfg = self._load_locked()
            except ConfigError:
                cfg = AppConfig()
            fn(cfg)
            self._cfg = cfg
            self._write_locked(cfg)
            self._mtime = self._mtime_locked()
