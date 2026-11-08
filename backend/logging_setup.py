"""结构化日志配置：统一格式，可选文件输出。"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO, log_file: Path | None = None) -> None:
    """初始化根日志。幂等：重复调用不会叠加 handler。"""
    root = logging.getLogger()
    if getattr(root, "_nav_sextant_configured", False):
        return
    root.setLevel(level)
    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)

    # 降低三方库噪音
    for noisy in ("httpx", "httpcore", "urllib3", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root._nav_sextant_configured = True  # type: ignore[attr-defined]
