# -*- coding: utf-8 -*-
"""独立图标布局：桌面 ListView 图标坐标枚举/恢复、显示/隐藏桌面图标。

实现说明（best-effort，失败仅告警不阻断切换）：
- 通过桌面 ListView 控件（SHELLDLL_DefView 下的 SysListView32 "FolderView"）枚举图标
  名称与屏幕坐标，保存为 JSON；切换后按名称匹配并 LVM_SETITEMPOSITION 恢复。
- 依赖同架构（64 位）跨进程 SendMessage 直接读写本进程缓冲区；
  Windows 10 22H2 起系统不再持久化图标布局，本模块负责“尽力恢复”，
  “自动排列”开启时恢复无效，需用户关闭自动排列。
"""
from __future__ import annotations

import ctypes
import math
import time
import winreg
from typing import Dict, List, Optional

from .constants import (LVM_GETITEMCOUNT, LVM_GETITEMPOSITION, LVM_GETITEMTEXTW,
                        LVM_SETITEMPOSITION, LVIF_TEXT, MEM_COMMIT, MEM_RELEASE,
                        MEM_RESERVE, PAGE_READWRITE, PROCESS_QUERY_INFORMATION,
                        PROCESS_VM_OPERATION, PROCESS_VM_READ, PROCESS_VM_WRITE,
                        REG_EXPLORER_ADVANCED, REG_HIDE_ICONS_NAME)
from .logger_setup import get_logger

log = get_logger("icon_layout")

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# 设置 argtypes/restype，避免 64 位句柄/指针截断
_user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
_user32.FindWindowW.restype = ctypes.c_void_p
_user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
_user32.FindWindowExW.restype = ctypes.c_void_p
_user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
_user32.SendMessageW.restype = ctypes.c_ssize_t
_user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
_user32.GetWindowThreadProcessId.restype = ctypes.c_uint
_user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.EnumWindows.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.GetClientRect.restype = ctypes.c_int
try:
    _user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = ctypes.c_longlong
except AttributeError:  # 32 位系统退化为 GetWindowLongW
    _user32.GetWindowLongPtrW = _user32.GetWindowLongW
    _user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long

GWL_STYLE = -16
LVS_AUTOARRANGE = 0x0100
_kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.VirtualAllocEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                     ctypes.c_uint, ctypes.c_uint]
_kernel32.VirtualAllocEx.restype = ctypes.c_void_p
_kernel32.VirtualFreeEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
_kernel32.VirtualFreeEx.restype = ctypes.c_int
_kernel32.WriteProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_kernel32.WriteProcessMemory.restype = ctypes.c_int
_kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_kernel32.ReadProcessMemory.restype = ctypes.c_int
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.restype = ctypes.c_int

# 跨进程读取 explorer 桌面 ListView 所需权限
_PROCESS_ACCESS = (PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION |
                   PROCESS_VM_READ | PROCESS_VM_WRITE)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class LVITEMW(ctypes.Structure):
    """64 位 LVITEMW 布局（pszText/lParam/puColumns/piColFmt 为指针）。"""
    _fields_ = [
        ("mask", ctypes.c_uint),
        ("iItem", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("stateMask", ctypes.c_uint),
        ("pszText", ctypes.c_void_p),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", ctypes.c_longlong),
        ("iIndent", ctypes.c_int),
        ("iGroupId", ctypes.c_int),
        ("cColumns", ctypes.c_uint),
        ("puColumns", ctypes.c_void_p),
        ("piColFmt", ctypes.c_void_p),
        ("iGroup", ctypes.c_int),
    ]


class IconLayoutError(Exception):
    pass


# ---------- 窗口查找 ----------
def _enum_worker_defview() -> Optional[int]:
    """Win10 多显示器场景下，SHELLDLL_DefView 可能位于 WorkerW 下。"""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _lparam):
        cls = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "WorkerW":
            defv = _user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            if defv:
                found.append(defv)
        return 1

    _user32.EnumWindows(cb, None)
    return found[0] if found else None


def find_desktop_listview() -> Optional[int]:
    """返回桌面图标 ListView 窗口句柄；未找到返回 None。"""
    try:
        progman = _user32.FindWindowW("Progman", None)
        defv = _user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None) if progman else None
        if not defv:
            defv = _enum_worker_defview()
        if not defv:
            return None
        lv = _user32.FindWindowExW(defv, None, "SysListView32", "FolderView")
        if not lv:
            lv = _user32.FindWindowExW(defv, None, "SysListView32", None)
        return lv or None
    except Exception as exc:  # noqa: BLE001
        log.warning("查找桌面 ListView 失败：%s", exc)
        return None


# ---------- 图标枚举 ----------
# 桌面 ListView 位于 explorer 进程内；ListView 消息（LVM_GETITEMTEXTW /
# LVM_GETITEMPOSITION）会把结果写入 lParam 指向的缓冲区，跨进程时直接传本地
# 指针无效。因此在 explorer 进程内 VirtualAllocEx 分配 LVITEM/POINT 结构，
# 经 WriteProcessMemory 写入后 SendMessage，再 ReadProcessMemory 读回。

def _open_explorer(hwnd: int):
    pid = ctypes.c_uint()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    return _kernel32.OpenProcess(_PROCESS_ACCESS, False, pid.value)


def _get_item_text(hwnd: int, index: int) -> str:
    text_chars = 512          # 图标名最大长度（字符）
    text_bytes = text_chars * 2  # UTF-16 字节数
    hproc = _open_explorer(hwnd)
    if not hproc:
        return ""
    remote = None
    try:
        size = ctypes.sizeof(LVITEMW())
        remote = _kernel32.VirtualAllocEx(hproc, None, size + text_bytes,
                                          MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not remote:
            return ""
        item = LVITEMW()
        item.mask = LVIF_TEXT
        item.iItem = index
        item.pszText = remote + size
        item.cchTextMax = text_chars
        written = ctypes.c_size_t()
        if not _kernel32.WriteProcessMemory(hproc, remote, ctypes.byref(item),
                                            size, ctypes.byref(written)):
            return ""
        if written.value != size:
            return ""
        if not _user32.SendMessageW(hwnd, LVM_GETITEMTEXTW, ctypes.c_void_p(index),
                                    ctypes.c_void_p(remote)):
            return ""
        local_buf = ctypes.create_string_buffer(text_bytes)
        read = ctypes.c_size_t()
        if not _kernel32.ReadProcessMemory(hproc, remote + size, local_buf,
                                           text_bytes, ctypes.byref(read)):
            return ""
        data = local_buf.raw[:read.value]
        if not data:
            return ""
        # 实测：部分系统（启用 UTF-8 Beta 区域设置）下桌面 ListView 写入的是
        # UTF-8 字节流；UTF-16LE 文本每个字符含一个 0x00（数量 ≈ 文本长度）。
        # 先去除缓冲尾部的空字节，再按文本内部 0x00 数量判别编码。
        stripped = data.rstrip(b"\x00")
        if not stripped:
            return ""
        if stripped.count(b"\x00") > 2:
            return stripped.decode("utf-16-le", errors="replace")
        return stripped.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        log.debug("读取图标文本失败（index=%d）：%s", index, exc)
        return ""
    finally:
        if remote:
            _kernel32.VirtualFreeEx(hproc, remote, 0, MEM_RELEASE)
        _kernel32.CloseHandle(hproc)


def _get_item_pos(hwnd: int, index: int) -> Optional[POINT]:
    hproc = _open_explorer(hwnd)
    if not hproc:
        return None
    remote = None
    try:
        remote = _kernel32.VirtualAllocEx(hproc, None, ctypes.sizeof(POINT()),
                                          MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not remote:
            return None
        if not _user32.SendMessageW(hwnd, LVM_GETITEMPOSITION, ctypes.c_void_p(index),
                                    ctypes.c_void_p(remote)):
            return None
        pt = POINT()
        read = ctypes.c_size_t()
        if not _kernel32.ReadProcessMemory(hproc, remote, ctypes.byref(pt),
                                           ctypes.sizeof(pt), ctypes.byref(read)):
            return None
        return pt
    except Exception as exc:  # noqa: BLE001
        log.debug("读取图标位置失败（index=%d）：%s", index, exc)
        return None
    finally:
        if remote:
            _kernel32.VirtualFreeEx(hproc, remote, 0, MEM_RELEASE)
        _kernel32.CloseHandle(hproc)


def enum_icons(retries: int = 3, delay: float = 0.4) -> List[Dict]:
    """枚举桌面图标：返回 [{"name": str, "x": int, "y": int}, ...]。

    桌面视图（SHELLDLL_DefView/SysListView32）在 explorer 刷新、远程/共享会话等
    场景下可能瞬时不可用，故带重试；连续失败抛 IconLayoutError。
    """
    last_error: Optional[Exception] = None
    for attempt in range(max(retries, 1)):
        try:
            lv = find_desktop_listview()
            if not lv:
                raise IconLayoutError("未找到桌面图标窗口（explorer 可能未就绪）")
            count = _user32.SendMessageW(lv, LVM_GETITEMCOUNT, None, None)
            if count and count > 0:
                if count > 10000:
                    count = 0
                items: List[Dict] = []
                for i in range(count):
                    text = _get_item_text(lv, i)
                    pos = _get_item_pos(lv, i)
                    if text and pos is not None:
                        items.append({"name": text, "x": int(pos.x), "y": int(pos.y)})
                if items:
                    return items
                last_error = IconLayoutError("桌面视图存在但读取图标文本失败（%d 项）" % count)
            else:
                last_error = IconLayoutError("桌面视图图标数为 0（explorer 视图可能正在重建）")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        if attempt + 1 < max(retries, 1):
            log.info("枚举桌面图标第 %d 次失败，重试：%s", attempt + 1, last_error)
            time.sleep(delay)
    raise last_error or IconLayoutError("枚举桌面图标失败")


def save_layout(pinned: bool = False) -> Dict:
    """保存当前桌面图标布局为可序列化结构（best-effort）。

    pinned=True 表示用户手动点“保存”建立的基准布局；pinned=False 表示
    切换时自动保存的临时布局。自动保存不得覆盖 pinned 基准布局，避免
    explorer 重启后 Windows 异步重排的错乱状态被逐次固化、偏差累积。
    """
    try:
        items = enum_icons()
    except IconLayoutError as exc:
        log.warning("保存图标布局失败：%s", exc)
        return {"saved": False, "items": [], "pinned": pinned, "suspicious": False,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
    # 错乱特征检测：多数图标集中在左半屏，却有个别图标被挤到最右两列
    # （Windows 连锁推移的典型结果）。提示用户不要把错乱状态存为基准。
    suspicious = False
    try:
        sw = _user32.GetSystemMetrics(0)
        if items and sw:
            edge = [it for it in items if it.get("x", 0) > sw * 0.85]
            left_ratio = sum(1 for it in items if it.get("x", 0) < sw * 0.5) / len(items)
            suspicious = bool(edge) and left_ratio > 0.7
    except Exception:  # noqa: BLE001
        suspicious = False
    return {
        "saved": True,
        "items": items,
        "count": len(items),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pinned": pinned,
        "suspicious": suspicious,
        "note": "绝对坐标，分辨率变化时可能偏移；自动排列开启时恢复无效",
    }


def list_icon_names() -> List[str]:
    """返回当前桌面视图显示的图标名称列表；无法枚举时返回 []。"""
    try:
        items = enum_icons()
    except IconLayoutError:
        return []
    return [it.get("name", "") for it in items if it.get("name")]


def view_contains(name: str) -> bool:
    """桌面视图是否包含指定图标名称（用于验证视图是否已切换到新文件夹）。"""
    names = list_icon_names()
    found = name in names
    if found:
        log.info("视图校验：桌面视图已显示标记文件 %s（视图已切换）", name)
    else:
        log.info("视图校验：桌面视图未显示 %s（共 %d 个图标），视图可能未切换",
                 name, len(names))
    return found


def _wait_icons_settled(expected: Optional[int] = None, stable_rounds: int = 3,
                         delay: float = 0.3) -> List[Dict]:
    """等待桌面图标完全加载并稳定：连续 stable_rounds 次枚举（名称+坐标）完全一致。

    explorer 重启后图标从文件夹异步加载，ListView 窗口出现 ≠ 图标就绪。
    不仅等名称集合稳定，还要等坐标稳定（Windows 自动排列过程中坐标会变）。
    返回稳定后的图标列表。
    """
    prev = None
    stable = 0
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            items = enum_icons(retries=1, delay=delay)
        except IconLayoutError:
            time.sleep(delay)
            continue
        # 名称+坐标都做成可比较的 tuple
        sig = tuple(sorted((it["name"], it.get("x", -1), it.get("y", -1)) for it in items))
        # expected：保存布局里的图标数。桌面视图实际 = 用户桌面 + 公共桌面，
        # 公共桌面图标不在保存列表里但会显示在 ListView 中，因此数量必然 >= expected。
        # 只要枚举出的图标数 >= expected，就认为保存的图标都已加载。
        if expected is not None and len(items) < expected:
            stable = 0
        elif sig == prev:
            stable += 1
            if stable >= stable_rounds:
                return items
        else:
            stable = 0
        prev = sig
        time.sleep(delay)
    try:
        return enum_icons(retries=1, delay=0.1)
    except IconLayoutError:
        return []


def apply_layout(layout: Optional[Dict]) -> int:
    """按名称匹配恢复图标坐标，返回成功恢复的数量（best-effort）。

    直接按名称匹配 index 设置保存坐标（不先移到屏幕外/左上角堆叠——
    实测 Windows 不允许图标重叠，批量移到 (0,0) 后会自动重排并改变
    ListView 内部 index 顺序，导致 name2index 失效、整列错位）。
    先等待图标完全加载稳定（名称+坐标都稳定），apply 完成后再验证一次，
    若 Windows 又自动重排则重试一次归位。
    """
    if not layout or not layout.get("saved"):
        return 0
    items = layout.get("items") or []
    if not items:
        return 0
    lv = find_desktop_listview()
    if not lv:
        log.warning("恢复图标布局失败：未找到桌面 ListView")
        return 0

    # 检测“自动排列图标”：开启时 LVM_SETITEMPOSITION 会被 Windows 立即
    # 吸附到网格，坐标无法固定——这是布局恢复间歇性失败的首要原因。
    try:
        style = _user32.GetWindowLongPtrW(ctypes.c_void_p(lv), GWL_STYLE)
        if style & LVS_AUTOARRANGE:
            log.warning("桌面开启了“自动排列图标”，图标坐标无法固定，"
                        "布局恢复很可能无效；请右键桌面→查看→取消“自动排列图标”")
    except Exception:  # noqa: BLE001
        pass

    # 等待图标完全加载稳定（数量、名称、坐标都稳定后再操作）
    expected = len(items)
    settled = _wait_icons_settled(expected=expected, stable_rounds=3, delay=0.3)
    if not settled:
        log.warning("图标布局恢复：等待图标加载超时")
        return 0
    log.info("等待图标加载完成：%d 个图标就绪", len(settled))

    try:
        count = _user32.SendMessageW(lv, LVM_GETITEMCOUNT, None, None)
    except Exception:  # noqa: BLE001
        return 0
    if count <= 0 or count > 10000:
        return 0

    def _set_pos(index: int, x: int, y: int) -> bool:
        try:
            lpx = int(x) & 0xFFFF
            lpy = int(y) & 0xFFFF
            lp = (lpy << 16) | lpx
            return bool(_user32.SendMessageW(lv, LVM_SETITEMPOSITION,
                                              ctypes.c_void_p(index),
                                              ctypes.c_void_p(lp)))
        except Exception:  # noqa: BLE001
            return False

    def _client_size():
        class _RC(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                        ("r", ctypes.c_long), ("b", ctypes.c_long)]
        rc = _RC()
        try:
            _user32.GetClientRect(ctypes.c_void_p(lv), ctypes.byref(rc))
            return rc.r - rc.l, rc.b - rc.t
        except Exception:  # noqa: BLE001
            return 1366, 768

    def _grid_step():
        # 从保存坐标推断网格步长（相邻去重坐标之差的最小正值），
        # 并夹到经验范围，避免异常值。
        xs = sorted({it.get("x", 0) for it in items})
        ys = sorted({it.get("y", 0) for it in items})
        dx = [b - a for a, b in zip(xs, xs[1:]) if b - a > 1]
        dy = [b - a for a, b in zip(ys, ys[1:]) if b - a > 1]
        sx = max(60, min(min(dx) if dx else 75, 120))
        sy = max(80, min(min(dy) if dy else 99, 130))
        return sx, sy

    def _enum_index():
        try:
            n = _user32.SendMessageW(lv, LVM_GETITEMCOUNT, None, None)
        except Exception:  # noqa: BLE001
            n = 0
        m = {}
        for i in range(max(int(n or 0), 0)):
            t = _get_item_text(lv, i)
            if t:
                m[t] = i
        return m

    def _place_all() -> int:
        """两阶段疏散归位。

        explorer 重启后图标被 Windows 紧凑排列（不留空洞）；若直接把图标
        移回“带空洞”的目标布局，目标格暂时被别的图标占据，Windows 不允许
        重叠会发生连锁推移，把最边缘图标挤到新列/新行。
        阶段1：把全部图标疏散到右侧一块与目标区不重叠、彼此等间距不重叠
        的临时网格（不触发自动重排、不改变名称集合）；
        阶段2：目标格全部空出后，再按保存坐标归位，任意顺序都不会推移。
        """
        step_x, step_y = _grid_step()
        cw, ch = _client_size()
        idx = _enum_index()
        if not idx:
            return 0
        all_names = list(idx.keys())
        n = len(all_names)

        # ---- 在全屏网格中搜索与目标不重叠的空闲格 ----
        # 网格原点取目标坐标的最小 x/y（通常为 13,2），与系统网格对齐。
        x0 = min((it.get("x", 0) for it in items), default=13)
        y0 = min((it.get("y", 0) for it in items), default=2)
        cmax = int((cw - x0 - step_x) // step_x)
        rmax = int((ch - y0 - step_y) // step_y)
        # 标记目标占用的网格格（量化到最近列/行）
        occupied = set()
        for it in items:
            gc = int(round((it.get("x", x0) - x0) / step_x))
            gr = int(round((it.get("y", y0) - y0) / step_y))
            occupied.add((gc, gr))
        # 从最右列向左、每列从上到下收集空闲格（优先远离左侧目标区）
        free_slots = []
        for c in range(cmax, -1, -1):
            for r in range(0, rmax + 1):
                if (c, r) not in occupied:
                    free_slots.append((int(x0 + c * step_x), int(y0 + r * step_y)))
            if len(free_slots) >= n:
                break

        if len(free_slots) >= n:
            slots = free_slots[:n]
            # 阶段1：全部图标疏散到空闲格（互不重叠、不压目标）
            moved = 0
            for name, (tx, ty) in zip(all_names, slots):
                if _set_pos(idx[name], tx, ty):
                    moved += 1
            time.sleep(0.45)
            idx = _enum_index()  # 重新建立名称→index，防止内部顺序变化
            # 阶段2：目标格已全部空出，按保存坐标归位，任意顺序都不推移
            ok = 0
            for it in items:
                i = idx.get(it.get("name"))
                if i is None:
                    continue
                if _set_pos(i, it.get("x", 0), it.get("y", 0)):
                    ok += 1
            log.info("两阶段归位：疏散 %d 个、归位 %d 个（步长 %dx%d，空闲格 %d）",
                     moved, ok, step_x, step_y, len(free_slots))
            return ok

        # 图标多到全屏网格都放不下（极少见）：回退直接按名归位
        log.warning("全屏空闲格不足（需 %d，有 %d），回退直接归位", n, len(free_slots))
        ok = 0
        for it in items:
            i = idx.get(it.get("name"))
            if i is None:
                continue
            if _set_pos(i, it.get("x", 0), it.get("y", 0)):
                ok += 1
        return ok

    target_map = {it["name"]: (it.get("x", 0), it.get("y", 0)) for it in items}

    def _count_mismatched():
        try:
            cur = enum_icons(retries=1, delay=0.1)
        except IconLayoutError:
            return None, 0
        bad = 0
        for it in cur:
            t = target_map.get(it["name"])
            if t is None:
                continue
            if abs(it.get("x", 0) - t[0]) > 20 or abs(it.get("y", 0) - t[1]) > 20:
                bad += 1
        return cur, bad

    # 多轮归位验证：explorer 重启后 Windows 的异步自动重排可能持续 2~3 秒，
    # 单次 sleep 1 秒验证可能漏掉更晚发生的重排。最多归位 4 轮，每轮间隔
    # 0.8 秒复核，连续达标（偏差数 <= 阈值）即提前结束。
    ok = 0
    threshold = 2
    for round_idx in range(4):
        ok = _place_all()
        try:
            _user32.InvalidateRect(lv, None, True)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.8)
        cur, bad = _count_mismatched()
        if cur is None:
            break
        threshold = max(2, len(cur) // 5)
        if bad <= threshold:
            if round_idx > 0:
                log.info("第 %d 轮复核：图标位置已稳定（偏差 %d 个）", round_idx + 1, bad)
            break
        log.warning("第 %d 轮复核：%d/%d 个图标坐标偏差过大，重新归位",
                    round_idx + 1, bad, len(cur))
    else:
        log.warning("多轮归位后仍有图标坐标偏差（可能开启了自动排列）")

    if ok:
        log.info("已恢复 %d 个桌面图标位置（直接按名恢复，含多轮验证）", ok)
    else:
        log.warning("图标布局恢复 0 个（可能开启了自动排列或图标名称不匹配）")
    return ok


# ---------- 显示/隐藏桌面图标 ----------
def get_hide_icons() -> bool:
    """返回是否隐藏桌面图标（HideIcons=1 为隐藏）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_EXPLORER_ADVANCED, 0,
                            winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, REG_HIDE_ICONS_NAME)
            return bool(value)
    except OSError:
        return False


def set_hide_icons(hide: bool) -> None:
    """设置显示/隐藏桌面图标。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_EXPLORER_ADVANCED, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, REG_HIDE_ICONS_NAME, 0, winreg.REG_DWORD, 1 if hide else 0)
        log.info("桌面图标%s", "隐藏" if hide else "显示")
    except OSError as exc:
        raise IconLayoutError("写入 HideIcons 失败：%s" % exc)


def apply_show_icons(show: bool) -> None:
    """切换时按目标桌面设置应用显示/隐藏。"""
    try:
        set_hide_icons(not show)
    except IconLayoutError as exc:
        log.warning("应用桌面图标显示设置失败：%s", exc)
