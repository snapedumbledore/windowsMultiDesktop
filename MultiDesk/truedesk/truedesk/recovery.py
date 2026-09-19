# -*- coding: utf-8 -*-
"""异常恢复：未完成切换事务的补完/回滚、注册表状态自检。"""
from __future__ import annotations

import os
from typing import Optional

from .config import ConfigManager
from .logger_setup import get_logger
from .redirect import RedirectEngine, SwitchResult

log = get_logger("recovery")


class RecoveryRunner:
    def __init__(self, cm: ConfigManager, test_mode: bool = False):
        self.cm = cm
        self.test_mode = test_mode
        self.engine = RedirectEngine(cm, test_mode=test_mode)

    def run(self) -> Optional[SwitchResult]:
        """执行启动恢复流程：补完或回滚未完成切换。"""
        try:
            result = self.engine.complete_or_rollback()
        except Exception as exc:  # noqa: BLE001
            log.error("启动恢复流程失败：%s", exc)
            return None
        try:
            self._recover_public_desktop()
        except Exception as exc:  # noqa: BLE001
            log.warning("启动恢复公共桌面失败（稍后可手动 truedesk isolate off 重置）：%s", exc)
        return result

    def _recover_public_desktop(self) -> None:
        """隔离模式下启动自检：HKLM 公共桌面应指向当前逻辑桌面；不一致或存在
        遗留 pending 请求时补同步。"""
        import os

        from .isolation import PublicIsolation, public_dir, read_common_desktop
        iso = PublicIsolation(self.cm, test_mode=self.test_mode)
        if not iso.is_isolated():
            # 未启用隔离但存在遗留 pending（例如提权失败后关闭隔离）：清掉
            pending = os.path.join(self.cm.data_root, "pending_public.json")
            if os.path.exists(pending):
                try:
                    os.remove(pending)
                    log.info("已清除遗留的公共桌面待应用请求")
                except OSError:
                    pass
            return
        # 存在遗留 pending：补触发（提权子进程会完成写 HKLM）
        pending = os.path.join(self.cm.data_root, "pending_public.json")
        cur = self.engine.store.current()
        if cur is None:
            return
        target = public_dir(self.cm.data_root, cur.id)
        try:
            values = read_common_desktop()
            current_target = values.get("shell_folders_common") or values.get("user_shell_folders_common") or ""
            cur_real = os.path.normpath(os.path.expandvars(current_target))
            expect = os.path.normpath(target)
            if os.path.exists(pending) or os.path.normcase(cur_real) != os.path.normcase(expect):
                if self.test_mode:
                    iso.sync_for_desktop(cur.id)
                elif os.path.exists(pending):
                    from .isolation import trigger_task
                    trigger_task(self.cm.data_root)
                else:
                    iso.sync_for_desktop(cur.id)
                log.info("公共桌面已与当前逻辑桌面同步：%s", target)
        except Exception as exc:  # noqa: BLE001
            log.warning("公共桌面自检失败：%s", exc)

    def check_desktop_path(self) -> bool:
        """自检：注册表指向的桌面路径是否存在；异常时告警并提示恢复。"""
        try:
            from .registry_backup import read_desktop_paths
            paths = read_desktop_paths()
            value = paths.get("shell_folders") or paths.get("user_shell_folders")
            if not value:
                log.warning("注册表中未读取到桌面路径")
                return False
            real = os.path.normpath(os.path.expandvars(value))
            if not os.path.isdir(real):
                log.warning("桌面路径不存在：%s（可执行 truedesk restore 恢复）", real)
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("桌面路径自检失败：%s", exc)
            return False
