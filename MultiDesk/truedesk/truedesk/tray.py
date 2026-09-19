# -*- coding: utf-8 -*-
"""系统托盘：pystray 常驻，右键菜单切换/管理/设置/退出，图标标识当前桌面。"""
from __future__ import annotations

import threading
from typing import List, Optional

import pystray
from PIL import Image, ImageDraw

from .config import ConfigManager
from .constants import APP_DISPLAY_NAME, APP_NAME
from .desktop_store import DesktopManager
from .logger_setup import get_logger
from .redirect import RedirectEngine
from .single_instance import OperationLock

log = get_logger("tray")


def _draw_icon(digit: Optional[str], color=(31, 66, 122)) -> Image.Image:
    """绘制 64x64 托盘图标：圆角蓝底 + 白色桌面编号。"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=14, fill=color)
    label = digit or "T"
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 34)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), label, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1] - 2), label,
           font=font, fill=(255, 255, 255))
    return img


class TrayApp:
    """托盘常驻程序。run_detached 在后台线程运行，主线程可做其他工作。"""

    def __init__(self, cm: ConfigManager, engine: RedirectEngine,
                 on_exit: Optional[callable] = None):
        self.cm = cm
        self.store = DesktopManager(cm)
        self.engine = engine
        self.on_exit = on_exit
        self._icon: Optional[pystray.Icon] = None
        self._lock = threading.Lock()

    # ---------- 菜单 ----------
    def _desktop_index(self) -> dict:
        """返回 {id: 序号} 用于图标角标。"""
        return {d.id: i + 1 for i, d in enumerate(self.store.list())}

    def _menu(self):
        desks = self.store.list()
        cur = self.store.current()
        idx = self._desktop_index()
        items = []
        # 切换到…子菜单
        sub = []
        for d in desks:
            checked = d.id == (cur.id if cur else None)
            label = ("✓ " if checked else "") + "%d %s" % (idx.get(d.id, 0), d.name)
            sub.append(pystray.MenuItem(label, lambda _i, did=d.id: self.switch_to(did),
                                        checked=lambda _i, c=checked: c))
        items.append(pystray.MenuItem("切换到…", pystray.Menu(*sub)))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("打开主界面", self.open_gui))
        items.append(pystray.MenuItem("恢复系统默认桌面", self.restore_default))
        items.append(pystray.MenuItem("开机自启", self.toggle_autostart,
                                      checked=lambda item: self._autostart_enabled()))
        items.append(pystray.MenuItem("公共桌面隔离（开启后各桌面图标完全独立）",
                                      self.toggle_isolation,
                                      checked=lambda item: self._isolation_enabled()))
        items.append(pystray.MenuItem("退出", self.quit))
        return pystray.Menu(*items)

    # ---------- 动作 ----------
    def switch_to(self, desk_id: str) -> None:
        def _run():
            try:
                with OperationLock():
                    self.engine.switch(desk_id)
                self.refresh()
                self._notify("已切换到“%s”" % self.store.get(desk_id).name)
            except Exception as exc:  # noqa: BLE001
                log.error("托盘切换失败：%s", exc)
                self._notify("切换失败：%s" % exc)
        threading.Thread(target=_run, daemon=True).start()

    def open_gui(self, _icon=None, _item=None) -> None:
        import subprocess
        import sys
        try:
            subprocess.Popen([sys.executable, "-m", "truedesk", "--gui"],
                             creationflags=0x00000008)  # DETACHED_PROCESS
        except OSError as exc:
            log.error("打开主界面失败：%s", exc)

    def restore_default(self, _icon=None, _item=None) -> None:
        def _run():
            try:
                from .registry_backup import RegistryBackup
                from . import refresher
                with OperationLock():
                    RegistryBackup(self.cm.data_root).restore_default()
                    refresher.refresh(self.cm.get().explorer_refresh)
                self.refresh()
                self._notify("已恢复系统默认桌面")
            except Exception as exc:  # noqa: BLE001
                log.error("恢复默认桌面失败：%s", exc)
                self._notify("恢复失败：%s" % exc)
        threading.Thread(target=_run, daemon=True).start()

    def toggle_autostart(self, _icon=None, _item=None) -> None:
        try:
            from . import autostart
            if autostart.is_enabled():
                autostart.disable()
            else:
                autostart.enable()
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            log.error("切换开机自启失败：%s", exc)

    def _autostart_enabled(self) -> bool:
        try:
            from . import autostart
            return autostart.is_enabled()
        except Exception:  # noqa: BLE001
            return False

    def toggle_isolation(self, _icon=None, _item=None) -> None:
        def _run():
            try:
                from .isolation import PublicIsolation
                iso = PublicIsolation(self.cm, test_mode=self.engine.test_mode)
                if iso.is_isolated():
                    msg = iso.disable()
                else:
                    msg = iso.enable()
                self.refresh()
                self._notify(msg)
            except Exception as exc:  # noqa: BLE001
                log.error("切换公共桌面隔离失败：%s", exc)
                self._notify("操作失败：%s" % exc)
        threading.Thread(target=_run, daemon=True).start()

    def _isolation_enabled(self) -> bool:
        try:
            return self.cm.get().public_redirect_mode == "isolated"
        except Exception:  # noqa: BLE001
            return False

    def quit(self, _icon=None, _item=None) -> None:
        log.info("用户从托盘退出 TrueDesk")
        if self._icon:
            self._icon.stop()
        if self.on_exit:
            self.on_exit()

    def _notify(self, text: str) -> None:
        if self._icon:
            try:
                self._icon.notify(text, APP_DISPLAY_NAME)
            except Exception:  # noqa: BLE001
                pass

    # ---------- 运行 ----------
    def refresh(self) -> None:
        """重建图标与菜单（当前桌面变化后调用）。"""
        with self._lock:
            if not self._icon:
                return
            cur = self.store.current()
            idx = self._desktop_index()
            digit = str(idx.get(cur.id)) if cur else None
            self._icon.icon = _draw_icon(digit)
            self._icon.menu = self._menu()
            try:
                self._icon.update_menu()
            except Exception:  # noqa: BLE001
                pass

    def run_detached(self) -> pystray.Icon:
        cur = self.store.current()
        idx = self._desktop_index()
        digit = str(idx.get(cur.id)) if cur else None
        icon = pystray.Icon(
            APP_NAME, _draw_icon(digit), APP_DISPLAY_NAME,
            menu=self._menu(),
        )
        self._icon = icon
        icon.run_detached()
        log.info("托盘已启动")
        return icon

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
