"""CPython stubs for `tpy.coro`."""
from tpy import Task, Waker, Poll


def poll_ready(value):
    return Poll.ready(value)


def poll_ready_none():
    return Poll.ready(None)


class _PollPendingMeta(type):
    """Allow `poll_pending[T]()` syntax in CPython."""
    def __getitem__(cls, t):
        return cls

    def __call__(cls):
        return Poll.pending()


class poll_pending(metaclass=_PollPendingMeta):
    """`poll_pending[T]()` / `poll_pending()` returns a Pending Poll."""


class _AwaitableMeta(type):
    def __getitem__(cls, t):
        return cls


class Awaitable(metaclass=_AwaitableMeta):
    """Subscriptable stub; no static-protocol enforcement under CPython."""
