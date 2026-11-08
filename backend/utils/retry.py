"""带指数退避与抖动的重试装饰器。

外部数据源（AKShare/LLM 网关）不稳定是常态：采集层所有网络调用都应经过这里。
"""
from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_on_error(
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """指数退避重试：delay = base * 2^n + jitter，超过 attempts 抛最后一次异常。"""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_err: BaseException | None = None
            for n in range(attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_err = e
                    if n == attempts - 1:
                        break
                    delay = min(base_delay * (2 ** n), max_delay)
                    delay *= 0.5 + random.random()  # 抖动
                    logger.warning("%s 第 %d/%d 次失败: %s，%.1fs 后重试",
                                   func.__name__, n + 1, attempts, e, delay)
                    time.sleep(delay)
            logger.error("%s 重试 %d 次后仍失败: %s", func.__name__, attempts, last_err)
            raise last_err  # type: ignore[misc]
        return wrapper
    return decorator
