"""Cooperative refresh budget, not an OS resource or blocking-I/O sandbox."""
from time import monotonic


class RefreshCancelled(ValueError):
    pass


class RefreshTimedOut(ValueError):
    pass


class RefreshBudget:
    def __init__(self, cancel, timeout_seconds):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
            raise ValueError('刷新预算必须为1到3600的整数秒')
        self.cancel = cancel
        self.timeout_seconds = timeout_seconds
        self.started = monotonic()
        self.deadline = self.started + timeout_seconds
        self.reason = None

    def is_set(self):
        if self.reason is None:
            if self.cancel.is_set(): self.reason = RefreshCancelled
            elif monotonic() >= self.deadline: self.reason = RefreshTimedOut
        return self.reason is not None

    def check(self):
        if self.is_set():
            raise self.reason('局部刷新已取消或超时，未发布新清单；基线保持不变')

    def sqlite_progress(self):
        # SQLite callbacks return an interrupt flag; exceptions would lose the cause.
        return int(self.is_set())

    def elapsed(self):
        return max(0, monotonic() - self.started)
