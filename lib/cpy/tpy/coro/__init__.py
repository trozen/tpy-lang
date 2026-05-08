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


import inspect as _inspect


class _PollOnceMeta(type):
    """Allow `poll_once[T](aw)` and bare `poll_once(aw)` syntaxes."""
    def __getitem__(cls, t):
        return cls

    def __call__(cls, aw):
        # CPython coroutine (from `async def f(): ...`): drive via the
        # standard `.send(None) -> StopIteration` protocol.
        if _inspect.iscoroutine(aw):
            try:
                aw.send(None)
                return Poll.pending()
            except StopIteration as si:
                return Poll.ready(si.value)
        # Hand-rolled awaitable (Future, user records with __poll__ method).
        return aw.__poll__(Waker())


class poll_once(metaclass=_PollOnceMeta):
    """`poll_once[T](aw)` / `poll_once(aw)` -- one-step driver."""
