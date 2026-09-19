# -*- coding: utf-8 -*-
"""TrueDesk 常量定义：注册表路径、Win32 API 常量、默认配置。"""
from __future__ import annotations

APP_NAME = "TrueDesk"
APP_DISPLAY_NAME = "真·多桌面"
APP_VERSION = "0.1.0"

# %APPDATA%\TrueDesk 数据根目录（config / backup / logs 均位于其下）
APPDATA_DIR_NAME = "TrueDesk"

# 注册表路径（仅 HKCU）
REG_USER_SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
REG_SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
REG_DESKTOP_NAME = "Desktop"
REG_CONTROL_DESKTOP = r"Control Panel\Desktop"
REG_WALLPAPER_NAME = "WallPaper"
REG_EXPLORER_ADVANCED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
REG_HIDE_ICONS_NAME = "HideIcons"
REG_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_SHELL_BAGS_DESKTOP = r"Software\Microsoft\Windows\Shell\Bags\1\Desktop"
REG_FFLAGS_NAME = "FFlags"

# 公共桌面隔离：HKLM 公共桌面路径（需管理员写；通过计划任务提权通道执行）
HKLM_USER_SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
HKLM_SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
COMMON_DESKTOP_NAME = "Common Desktop"
SCHEDULER_TASK_NAME = "TrueDeskApplyPublic"      # 提权写公共桌面路径的计划任务
PENDING_PUBLIC_FILE = "pending_public.json"       # 待应用公共桌面路径（提权通道输入）
COMMON_ORIGINAL_FILE = "common_desktop_original.json"  # HKLM 公共桌面原值备份

# 逻辑桌面默认目录
DEFAULT_ROOT_ENV = "%USERPROFILE%\\TrueDesk"
DESKTOPS_SUBDIR = "Desktops"

# 配置文件与目录名
CONFIG_FILE = "config.json"
BACKUP_DIR = "backup"
LOGS_DIR = "logs"
LOG_FILE = "log.txt"
COMMANDS_DIR = "commands"  # 预留：跨进程命令通道

# Win32 API 常量
SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x0001
SPIF_SENDCHANGE = 0x0002

SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST = 0x0000
SHCNF_FLUSHNOWAIT = 0x1000

WM_SETTINGCHANGE = 0x001A
HWND_BROADCAST = 0xFFFF

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
WM_HOTKEY = 0x0312

# ListView 消息
LVM_FIRST = 0x1000
LVM_GETITEMCOUNT = LVM_FIRST + 4
LVM_GETITEMTEXT = LVM_FIRST + 45
LVM_GETITEMTEXTW = LVM_FIRST + 45  # Unicode 版本宏值
LVM_GETITEMPOSITION = LVM_FIRST + 16
LVM_SETITEMPOSITION = LVM_FIRST + 15
LVM_GETITEMW = LVM_FIRST + 75
LVIF_TEXT = 0x0001
LVIF_PARAM = 0x0004
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04

# 系统支持下限：Windows 10 1903（Build 18362）
MIN_WIN10_BUILD = 18362

# 切换事务步骤
PENDING_STEP_BACKED_UP = "backed_up"      # 已备份
PENDING_STEP_WRITTEN = "written"          # 已写入注册表/联接点
PENDING_STEP_REFRESHED = "refreshed"      # 已通知刷新
PENDING_STEP_DONE = "done"                # 已完成

# 默认配置
DEFAULT_HOTKEY_PREFIX = "ctrl+alt"
DEFAULT_REFRESH_MODE = "auto"
