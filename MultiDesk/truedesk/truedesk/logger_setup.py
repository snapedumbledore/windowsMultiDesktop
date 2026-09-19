# -*- coding: utf-8 -*-
"""日志初始化：RotatingFileHandler 轮转，输出到 %APPDATA%\\TrueDesk\\logs\\truedesk.log。"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

from .constants import APP_NAME, LOG_FILE, LOGS_DIR

_configured = False
_loggers: dict = {}


def ensure_log_dir(data_root: str) -> str:
    log_dir = os.path.join(data_root, LOGS_DIR)
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def setup_logging(data_root: str, level: int = logging.INFO) -> None:
    """初始化根日志配置（幂等）。data_root 为 TrueDesk 数据根目录。"""
    global _configured
    if _configured:
        return
    log_dir = ensure_log_dir(data_root)
    path = os.path.join(log_dir, LOG_FILE)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(fmt)
    root = logging.getLogger(APP_NAME)
    root.setLevel(level)
    root.addHandler(handler)
    # 也输出到控制台（GUI 模式无控制台时自动忽略）
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    _configured = True


def get_logger(name: str = "truedesk") -> logging.Logger:
    return logging.getLogger("%s.%s" % (APP_NAME, name))
