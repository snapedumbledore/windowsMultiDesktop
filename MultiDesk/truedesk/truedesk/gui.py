# -*- coding: utf-8 -*-
"""主界面：tkinter 中文图形界面（FR-20），逻辑桌面增删改查与设置。"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import ConfigManager
from .constants import APP_DISPLAY_NAME, APP_NAME, APP_VERSION
from .desktop_store import DesktopError, DesktopManager
from .logger_setup import get_logger
from .redirect import RedirectEngine
from .single_instance import OperationLock

log = get_logger("gui")


class MainWindow:
    def __init__(self, cm: ConfigManager, engine: RedirectEngine):
        self.cm = cm
        self.store = DesktopManager(cm)
        self.engine = engine
        self._q: "queue.Queue" = queue.Queue()
        self.root = tk.Tk()
        self.root.title("%s v%s" % (APP_DISPLAY_NAME, APP_VERSION))
        self.root.geometry("760x560")
        self.root.minsize(680, 500)
        self._build_ui()
        self.refresh_list()
        self.root.after(150, self._poll)

    # ---------- UI ----------
    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        # 左侧：桌面列表
        left = ttk.Frame(main)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(left, font=("Microsoft YaHei UI", 11),
                                  activestyle="dotbox", selectmode="browse")
        self.listbox.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=sb.set)

        ops = ttk.Frame(left)
        ops.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for i, (t, cmd) in enumerate([
            ("新建", self.on_create), ("重命名", self.on_rename), ("删除", self.on_delete),
            ("设为默认", self.on_set_default), ("立即切换", self.on_switch),
        ]):
            b = ttk.Button(ops, text=t, command=cmd)
            b.grid(row=0, column=i, padx=3, sticky="ew")
            ops.columnconfigure(i, weight=1)

        # 右侧：设置区
        right = ttk.LabelFrame(main, text="当前桌面设置", padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        self.setup_label = ttk.Label(right, text="", wraplength=300)
        self.setup_label.pack(anchor="w", pady=(0, 6))

        ttk.Label(right, text="壁纸：").pack(anchor="w")
        wrow = ttk.Frame(right)
        wrow.pack(fill="x", pady=(2, 6))
        self.wallpaper_var = tk.StringVar()
        ttk.Entry(wrow, textvariable=self.wallpaper_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(wrow, text="浏览", width=6, command=self.on_pick_wallpaper).pack(side="left", padx=(4, 0))
        ttk.Button(wrow, text="应用", width=6, command=self.on_apply_wallpaper).pack(side="left", padx=(4, 0))
        ttk.Button(wrow, text="清除", width=6, command=self.on_clear_wallpaper).pack(side="left", padx=(4, 0))

        self.show_icons_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="显示桌面图标", variable=self.show_icons_var,
                        command=self.on_toggle_icons).pack(anchor="w", pady=4)
        self.autostart_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="开机自启（恢复上次桌面）", variable=self.autostart_var,
                        command=self.on_toggle_autostart).pack(anchor="w", pady=4)

        ttk.Button(right, text="保存当前图标布局到本桌面", command=self.on_save_layout).pack(fill="x", pady=4)
        ttk.Button(right, text="恢复本桌面图标布局", command=self.on_restore_layout).pack(fill="x", pady=4)
        ttk.Button(right, text="恢复系统默认桌面", command=self.on_restore_default).pack(fill="x", pady=4)

        bottom = ttk.Frame(right)
        bottom.pack(fill="x", side="bottom")
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)
        ttk.Button(bottom, text="导出配置", command=self.on_export).grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=4)
        ttk.Button(bottom, text="导入配置", command=self.on_import).grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=4)

        self.status = ttk.Label(self.root, text="就绪", anchor="w", relief="sunken")
        self.status.pack(fill="x", side="bottom")

    # ---------- 列表 ----------
    def refresh_list(self) -> None:
        self.listbox.delete(0, "end")
        cur = self.store.current()
        default = self.store.default()
        for i, d in enumerate(self.store.list(), 1):
            marks = []
            if default and d.id == default.id:
                marks.append("默认")
            if cur and d.id == cur.id:
                marks.append("当前")
            suffix = "（%s）" % "、".join(marks) if marks else ""
            self.listbox.insert("end", "%d  %s%s" % (i, d.name, suffix))
        self._refresh_setup()

    def selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        desks = self.store.list()
        idx = sel[0]
        return desks[idx] if 0 <= idx < len(desks) else None

    def _refresh_setup(self) -> None:
        d = self.selected()
        from . import autostart
        if d is None:
            self.setup_label.configure(text="请选择一个逻辑桌面")
            self.wallpaper_var.set("")
            return
        wp = d.wallpaper or ""
        self.setup_label.configure(text="桌面：%s\n路径：%s" % (d.name, d.expanded_path()))
        self.wallpaper_var.set(wp)
        self.show_icons_var.set(d.show_icons)
        try:
            self.autostart_var.set(autostart.is_enabled())
        except Exception:  # noqa: BLE001
            pass

    # ---------- 操作 ----------
    def _run_bg(self, fn, success_msg):
        self.status.configure(text="执行中…")

        def _work():
            try:
                with OperationLock():
                    result = fn()
                self._q.put(("ok", success_msg, result))
            except Exception as exc:  # noqa: BLE001
                self._q.put(("err", str(exc), None))

        threading.Thread(target=_work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, msg, result = self._q.get_nowait()
                if kind == "ok":
                    self.status.configure(text=msg)
                    if msg:
                        messagebox.showinfo(APP_DISPLAY_NAME, msg, parent=self.root)
                elif kind == "warn":
                    self.status.configure(text=msg)
                    messagebox.showwarning(APP_DISPLAY_NAME, msg, parent=self.root)
                else:
                    self.status.configure(text="失败：%s" % msg)
                    messagebox.showerror(APP_DISPLAY_NAME, msg, parent=self.root)
                self.refresh_list()
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def on_create(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("新建逻辑桌面")
        win.geometry("420x160")
        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="名称：").grid(row=0, column=0, sticky="w")
        name_var = tk.StringVar()
        ttk.Entry(f, textvariable=name_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(f, text="路径（留空使用默认）：").grid(row=1, column=0, sticky="w")
        path_var = tk.StringVar()
        ttk.Entry(f, textvariable=path_var).grid(row=1, column=1, sticky="ew", pady=4)
        f.columnconfigure(1, weight=1)

        def _do():
            name = name_var.get().strip()
            path = path_var.get().strip() or None
            if not name:
                messagebox.showwarning("提示", "请输入名称", parent=win)
                return
            try:
                with OperationLock():
                    d = self.store.create(name, path=path)
                win.destroy()
                self.status.configure(text="已创建：%s" % d.name)
                self.refresh_list()
            except DesktopError as exc:
                messagebox.showerror("创建失败", str(exc), parent=win)

        ttk.Button(f, text="创建", command=_do).grid(row=2, column=0, columnspan=2, pady=10)

    def on_rename(self) -> None:
        d = self.selected()
        if not d:
            messagebox.showwarning("提示", "请先选择逻辑桌面")
            return
        win = tk.Toplevel(self.root)
        win.title("重命名逻辑桌面")
        win.geometry("420x160")
        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="新名称：").grid(row=0, column=0, sticky="w")
        name_var = tk.StringVar(value=d.name)
        ttk.Entry(f, textvariable=name_var).grid(row=0, column=1, sticky="ew", pady=4)
        migrate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="同时迁移文件夹到新路径", variable=migrate_var).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=4)
        f.columnconfigure(1, weight=1)

        def _do():
            try:
                with OperationLock():
                    self.store.rename(d.id, name_var.get().strip(), migrate=migrate_var.get())
                win.destroy()
                self.refresh_list()
            except DesktopError as exc:
                messagebox.showerror("重命名失败", str(exc), parent=win)

        ttk.Button(f, text="确定", command=_do).grid(row=2, column=0, columnspan=2, pady=10)

    def on_delete(self) -> None:
        d = self.selected()
        if not d:
            messagebox.showwarning("提示", "请先选择逻辑桌面")
            return
        # FR-31：二次确认，默认不删除文件夹
        delete_folder = messagebox.askyesno(
            APP_DISPLAY_NAME,
            "确定删除逻辑桌面“%s”？\n\n是否同时删除其文件夹？\n（选择“否”则仅移除配置，保留文件夹）" % d.name,
            parent=self.root)
        try:
            with OperationLock():
                result = self.store.delete(d.id, delete_folder=bool(delete_folder))
        except DesktopError as exc:
            # 即使失败也丢弃内存缓存、按磁盘最新状态刷新列表，避免界面残留
            self.cm.reload()
            self.refresh_list()
            messagebox.showerror("删除失败", str(exc), parent=self.root)
            return
        # 删除成功（含幂等的“已不存在”）：丢弃缓存并刷新，避免界面仍显示已删桌面
        self.cm.reload()
        self.refresh_list()
        if result.get("already_gone"):
            self.status.configure(text="桌面“%s”已不存在，列表已刷新" % d.name)
            messagebox.showinfo(
                APP_DISPLAY_NAME,
                "桌面“%s”已不存在（可能已被删除），列表已刷新。" % d.name,
                parent=self.root)
        elif result.get("folder_warning"):
            self.status.configure(text="已删除配置：%s（文件夹保留）" % result.get("name", d.name))
            messagebox.showwarning(APP_DISPLAY_NAME, result["folder_warning"], parent=self.root)
        else:
            self.status.configure(text="已删除：%s" % result.get("name", d.name))

    def on_set_default(self) -> None:
        d = self.selected()
        if not d:
            messagebox.showwarning("提示", "请先选择逻辑桌面")
            return
        with OperationLock():
            self.store.set_default(d.id)
        self.refresh_list()
        self.status.configure(text="已将“%s”设为默认桌面" % d.name)

    def on_switch(self) -> None:
        d = self.selected()
        if not d:
            messagebox.showwarning("提示", "请先选择逻辑桌面")
            return
        self._run_bg(lambda: self.engine.switch(d.id),
                     "已切换到“%s”" % d.name)

    # ---------- 壁纸 ----------
    def _selected_desktop(self):
        d = self.selected()
        if not d:
            messagebox.showwarning("提示", "请先选择逻辑桌面", parent=self.root)
            return None
        return d

    def on_pick_wallpaper(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="选择壁纸",
            filetypes=[("图片", "*.jpg *.jpeg *.bmp *.png"), ("所有文件", "*.*")])
        if path:
            self.wallpaper_var.set(path)

    def on_apply_wallpaper(self) -> None:
        d = self._selected_desktop()
        if not d:
            return
        path = self.wallpaper_var.get().strip()
        if not path:
            messagebox.showwarning("提示", "请先选择壁纸图片", parent=self.root)
            return

        def _fn(cfg, did=d.id, path=path):
            for x in cfg.desktops:
                if x.id == did:
                    x.wallpaper = path
                    x.wallpaper_enabled = True

        def _work():
            from . import wallpaper as wp
            real = wp.validate_wallpaper_file(path)
            with OperationLock():
                self.cm.mutate(lambda cfg, did=d.id, real=real: _fn(cfg, did, real))
                wp.set_wallpaper(real)
            self._q.put(("ok", "已为“%s”应用壁纸" % d.name, None))

        threading.Thread(target=_work, daemon=True).start()

    def on_clear_wallpaper(self) -> None:
        d = self._selected_desktop()
        if not d:
            return
        with OperationLock():
            self.cm.mutate(lambda cfg, did=d.id: self._set_wallpaper_none(cfg, did))
        self.wallpaper_var.set("")
        self.refresh_list()

    @staticmethod
    def _set_wallpaper_none(cfg, did):
        for x in cfg.desktops:
            if x.id == did:
                x.wallpaper = None
                x.wallpaper_enabled = False

    # ---------- 显示设置 / 自启 ----------
    def on_toggle_icons(self) -> None:
        d = self._selected_desktop()
        if not d:
            return
        show = self.show_icons_var.get()

        def _work():
            from .icon_layout import apply_show_icons
            with OperationLock():
                self.cm.mutate(lambda cfg, did=d.id, show=show: self._set_show_icons(cfg, did, show))
                apply_show_icons(show)
            self._q.put(("ok", "", None))

        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _set_show_icons(cfg, did, show):
        for x in cfg.desktops:
            if x.id == did:
                x.show_icons = bool(show)

    def on_toggle_autostart(self) -> None:
        from . import autostart
        try:
            autostart.set_enabled(self.autostart_var.get())
        except OSError as exc:
            messagebox.showerror("失败", str(exc), parent=self.root)

    # ---------- 图标布局 ----------
    def on_save_layout(self) -> None:
        d = self._selected_desktop()
        if not d:
            return

        def _work():
            from .icon_layout import save_layout
            layout = save_layout(pinned=True)
            if not layout.get("saved"):
                self._q.put(("err", "未找到桌面图标窗口，布局未保存", None))
                return
            with OperationLock():
                self.cm.mutate(lambda cfg, did=d.id, layout=layout: self._set_layout(cfg, did, layout))
            count = layout.get("count", 0)
            if layout.get("suspicious"):
                self._q.put(("warn",
                             "已保存 %d 个图标位置。\n\n但检测到有图标位于屏幕最边缘、"
                             "与多数图标分离，这通常是 Windows 自动重排造成的错乱状态。\n"
                             "建议先把图标拖回期望位置再点保存，否则错乱布局会被当作基准。"
                             % count, None))
            else:
                self._q.put(("ok", "已保存 %d 个图标位置" % count, None))

        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _set_layout(cfg, did, layout):
        for x in cfg.desktops:
            if x.id == did:
                x.icon_layout = layout

    def on_restore_layout(self) -> None:
        d = self._selected_desktop()
        if not d:
            return

        def _work():
            import time
            from .icon_layout import apply_layout
            time.sleep(1.0)
            n = apply_layout(d.icon_layout)
            self._q.put(("ok" if n else "err",
                         "已恢复 %d 个图标位置" % n if n else "恢复 0 个（可能开启了自动排列）", None))

        threading.Thread(target=_work, daemon=True).start()

    # ---------- 恢复默认 / 导入导出 ----------
    def on_restore_default(self) -> None:
        if not messagebox.askyesno(APP_DISPLAY_NAME, "将系统桌面恢复为默认位置，是否继续？", parent=self.root):
            return

        def _work():
            from . import refresher
            from .registry_backup import RegistryBackup
            with OperationLock():
                RegistryBackup(self.cm.data_root).restore_default()
                refresher.refresh(self.cm.get().explorer_refresh)
            self._q.put(("ok", "已恢复系统默认桌面", None))

        threading.Thread(target=_work, daemon=True).start()

    def on_export(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出配置", defaultextension=".json",
            initialfile="truedesk_export.json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        from .cli import _export_cmd
        try:
            _export_cmd(path, self.cm)
            messagebox.showinfo(APP_DISPLAY_NAME, "已导出到：%s" % path, parent=self.root)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)

    def on_import(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="导入配置",
                                          filetypes=[("JSON", "*.json")])
        if not path:
            return
        from .cli import _import_cmd
        try:
            _import_cmd(path, self.cm)
            self.refresh_list()
            messagebox.showinfo(APP_DISPLAY_NAME, "导入完成", parent=self.root)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导入失败", str(exc), parent=self.root)

    # ---------- 主循环 ----------
    def run(self) -> None:
        self.root.mainloop()


def run_gui(cm: ConfigManager, engine: RedirectEngine) -> int:
    win = MainWindow(cm, engine)
    win.run()
    return 0
