# resilience.py — 熔断器 + 指数退避重试（同步版）
import time
import random
from enum import Enum
from utils import log


class CircuitState(Enum):
    CLOSED = "closed"        # 正常
    OPEN = "open"            # 熔断，拒绝请求
    HALF_OPEN = "half_open"  # 半开，试探恢复


class CircuitBreakerOpen(Exception):
    """熔断器开启时抛出"""
    pass


class CircuitBreaker:
    """三态熔断器：CLOSED → OPEN → HALF_OPEN

    同步版本，适配项目现有 threading 架构。
    """

    def __init__(self, name, failure_threshold=5, recovery_timeout=60, success_threshold=2):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.successes = 0
        self.last_failure_time = 0

    def call(self, func, *args, **kwargs):
        """同步调用 func，带熔断保护"""
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.successes = 0
                log("[{}] 熔断器半开，试探恢复".format(self.name))
            else:
                raise CircuitBreakerOpen(
                    "[{}] 熔断器开启，{}s 后重试".format(self.name, self.recovery_timeout)
                )
        try:
            result = func(*args, **kwargs)
            if self.state == CircuitState.HALF_OPEN:
                self.successes += 1
                if self.successes >= self.success_threshold:
                    self.state = CircuitState.CLOSED
                    self.failures = 0
                    log("[{}] 熔断器恢复（CLOSED）".format(self.name))
            return result
        except CircuitBreakerOpen:
            raise
        except Exception:
            self.failures += 1
            self.last_failure_time = time.time()
            if self.failures >= self.failure_threshold:
                if self.state != CircuitState.OPEN:
                    self.state = CircuitState.OPEN
                    log("[{}] 熔断器开启（连续失败 {} 次）".format(self.name, self.failures))
            raise

    @property
    def is_open(self):
        return self.state == CircuitState.OPEN


def retry_with_backoff(func, max_retries=3, base_delay=1.0, max_delay=30.0, retryable=(Exception,)):
    """指数退避 + 随机抖动（同步版）

    Args:
        func: 无参数的可调用对象（用 lambda 包裹参数）
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
        retryable: 可重试的异常类型

    Returns:
        func 的返回值

    Raises:
        最后一次异常
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except retryable as e:
            last_exception = e
            if attempt == max_retries:
                break
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            total = delay + jitter
            log("重试 {}/{}，等待 {:.1f}s: {}".format(attempt + 1, max_retries, total, e))
            time.sleep(total)
    raise last_exception
