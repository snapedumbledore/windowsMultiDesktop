# TrueDesk 真·多桌面 — 构建与使用说明

基于开发方案（`../TrueDesk开发方案.docx`）实现的 Windows 多逻辑桌面工具：
每个逻辑桌面拥有独立的桌面文件夹、快捷方式、壁纸与图标布局；切换后系统桌面只显示当前逻辑桌面内容。

## 目录结构

```
truedesk/
├── truedesk/            # 源码包（见下）
├── tests/               # pytest 单元测试（模拟注册表，不触碰真实系统）
├── assets/              # 图标等资源
├── requirements.txt     # 依赖
├── build.spec           # PyInstaller 打包配置
└── build.ps1            # 一键打包脚本
```

源码模块：`constants`（常量）、`config`（配置 JSON）、`desktop_store`（逻辑桌面管理）、
`redirect`（切换引擎：registry/junction 双后端 + 事务）、`registry_backup`（注册表备份/恢复）、
`refresher`（SHChangeNotify / 重启 explorer）、`wallpaper`（独立壁纸）、`icon_layout`（图标布局）、
`hotkey`（Ctrl+Alt+1..9）、`tray`（系统托盘）、`gui`（主界面）、`cli`（命令行）、
`single_instance`（单实例/操作锁）、`autostart`（开机自启）、`recovery`（崩溃恢复）、`logger_setup`（日志）。

## 环境要求

- Windows 10 1903+ / Windows 11（检测不满足时自动提示退出）
- Python 3.10+（开发环境 3.14）
- 常规桌面切换仅修改 HKCU 与用户目录，**不需要管理员权限**；
  公共桌面**彻底隔离**（isolate）需一次管理员权限（见下文）

## 安装与运行（开发模式）

```powershell
cd truedesk
python -m pip install -r requirements.txt

# 托盘常驻（默认入口）
python -m truedesk

# 打开主界面
python -m truedesk --gui

# 命令行
python -m truedesk list
python -m truedesk create 工作
python -m truedesk switch 工作
python -m truedesk status
python -m truedesk restore          # 恢复系统默认桌面
python -m truedesk config redirect_mode registry
python -m truedesk autostart on     # 开机自启
```

## 安全试用（不修改真实注册表）

```powershell
python -m truedesk --test-mode status
python -m truedesk --test-mode --gui
```

测试模式使用 `%TEMP%\TrueDeskTest` 数据目录，切换引擎走内存模拟后端，
**不会修改真实注册表**，可用于先熟悉界面与命令。

## 运行单元测试

```powershell
python -m pytest tests -v
```

测试全部使用临时目录与模拟后端，不触碰真实注册表/桌面。

## 打包为独立 exe

```powershell
cd truedesk
python -m pip install pyinstaller
.\build.ps1
# 产物：dist\TrueDesk.exe（单文件、无控制台）
```

## 首次运行行为（FR-01）

- 备份桌面路径注册表原值到 `%APPDATA%\TrueDesk\backup\`
- 创建“默认”逻辑桌面，其路径指向**当前系统桌面**（不移动任何用户文件）
- 之后创建的每个逻辑桌面拥有独立文件夹 `%USERPROFILE%\TrueDesk\Desktops\<名称>`

## 主要配置（%APPDATA%\TrueDesk\config.json）

| 配置项 | 可选值 | 说明 |
|---|---|---|
| redirect_mode | registry / junction | 重定向后端，默认 registry |
| explorer_refresh | auto / notify / restart / none | 切换后刷新策略，默认 auto |
| hotkey_prefix | ctrl+alt 等 | 全局热键修饰键 |
| restore_on_exit | true / false | 退出时恢复设置 |

## 运行日志（问题排查）

- 位置：`%APPDATA%\TrueDesk\logs\log.txt`（测试模式为 `%TEMP%\TrueDeskTest\logs\log.txt`）
- 内容：切换（前后桌面、视图校验结果、是否重启 explorer）、创建/删除/重命名、
  注册表备份与恢复、壁纸/图标布局、错误与回滚，均带时间戳记录
- 大小：单文件 5 MB 自动轮转，保留 5 个历史文件

## 视图切换机制（重要）

Windows 桌面视图由 explorer.exe 缓存，仅修改注册表 + 发送 Shell 通知**不会**让桌面视图
真正指向新文件夹。因此切换引擎按以下逻辑保证视图真正切换：

1. 切换后向目标桌面文件夹写入一个**可见**的临时校验标记文件（随机名，如 TDViewCheck_xxxx.tmp）；
2. 轮询桌面视图是否显示该标记（验证视图已切换；不能使用隐藏文件作标记，
   因为桌面默认不显示隐藏文件会导致校验恒失败）；
3. 视图未切换时，`auto`/`restart` 模式自动重启 explorer（任务栏与文件管理器窗口短暂消失后恢复），
   重启后视图必然重新枚举新文件夹；
4. 校验完成后移除标记文件（切换瞬间桌面可能短暂闪现该标记文件，属正常现象）。

因此默认 `auto` 模式下视图未及时切换时会自动重启一次 explorer，属预期行为
（FR-12 允许短暂闪烁；已提前提示）。

## 公共桌面彻底隔离（v0.1.1 新增，需一次管理员权限）

Windows 桌面视图 = **用户桌面文件夹（可重定向） + 公共桌面（%PUBLIC%\Desktop） + 系统图标**。
`C:\Users\Public\Desktop` 中的快捷方式（软件安装时选择“为所有用户”生成，如 AweSun、
FlClash、通义千问、商汤小浣熊、网易发烧游戏等）**默认会出现在每一个逻辑桌面上**。

**彻底隔离**：TrueDesk 将 HKLM 的公共桌面路径（`User Shell Folders` 与 `Shell Folders`
两键的 `Common Desktop`）重定向到 `%APPDATA%\TrueDesk\Public\<桌面id>`，每个逻辑桌面
拥有**独立**的公共图标副本——在任一桌面删除/新增公共图标，不影响其他桌面。

- 启用：`python -m truedesk isolate on`（首次需管理员；自动通过计划任务
  `TrueDeskApplyPublic`（/RL HIGHEST）提权写 HKLM，后续切换**无需**再确认）
- 关闭：`python -m truedesk isolate off`（还原系统公共桌面路径）
- 状态：`python -m truedesk isolate status`
- 首次启用会为每个逻辑桌面复制当前公共桌面图标（不移动、不删除源文件）；
  HKLM 原值备份于 `%APPDATA%\TrueDesk\backup\common_desktop_original.json`，
  `truedesk restore` 会一并还原公共桌面路径。
- 影响范围：公共桌面为机器级设置，启用后本机所有用户共享的公共桌面路径
  指向当前逻辑桌面的副本（单用户家用机无感，多用户机器请知悉）。
- 系统图标（回收站、“此电脑”等）仍显示于所有桌面，无法隔离。

> 若 UAC 弹窗无法正常弹出（受限环境），请以管理员身份运行一次：
> `python -m truedesk isolate on --elevated`

## 已知限制（如实声明）

- **删除桌面图标 = 删除真实文件**：切换后视图若未真正切换（如使用 `notify`/`none` 模式），
  删除图标会删除对应桌面文件夹中的真实文件；即使之后“恢复默认桌面”，被删除的文件也不会恢复
  （可从回收站还原）。请使用默认 `auto` 模式并核对切换后桌面内容再操作。
- 图标布局为“尽力恢复”：Windows 10 22H2 起系统不再持久化桌面图标布局；
  恢复需关闭“自动排列图标”，分辨率变化时坐标可能偏移。图标枚举/恢复采用
  explorer 进程内分配内存的跨进程读取，并兼容 UTF-8 系统区域设置（中文名正常）。
  远程/共享会话下桌面视图窗口可能瞬时不可用，枚举带自动重试，仍失败时仅记录日志不阻断切换。
- junction 模式首次启用时不会自动迁移原桌面文件，需先手动移动或改用 registry 模式。
- 剪贴板为系统全局共享，跨桌面复制粘贴正常（符合需求 FR-15）。
