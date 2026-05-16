# tpy: cpp_namespace("tpystd::coro")
"""Coroutine / async runtime primitives.

Module name borrowed from the design-doc's `Coroutine[T]` terminology
(see Rust `std::task` / Tokio `tokio::task` for prior art).
"""
from .._typing import Protocol
from .._bootstrap._decorators import Own, dynamic
from .._builtins._exceptions import CancelledError
from .._core import Waker, Poll


# Structural awaitable. Distinct from `typing.Awaitable[T]` (CPython's
# `__await__`-based shape) -- TPy uses `__poll__(Waker) -> Poll[T]`.
# The dunder name matches how other TPy/typing structural protocols
# spell their required methods (`__iter__`, `__hash__`, `__lt__`, ...)
# and signals "runtime protocol method -- prefer `await` / `poll_once`
# to direct calls".
class Awaitable[T](Protocol):
    def __poll__(self, waker: Waker) -> Own[Poll[T]]: ...


# `AsyncFrame[T]` / `AnyTask` (@dynamic protocols used by the task
# machinery) live in `asyncio._executor` -- they sit on tplib, which
# isn't reachable from this implicit-stdlib module.


def poll_ready[T](value: Own[T]) -> Own[Poll[T]]:
    return Poll[T].ready(value)


def poll_pending[T]() -> Own[Poll[T]]:
    return Poll[T].pending()


# Separate factory because `poll_ready[T](value)` needs an `Own[T]`
# argument that `Own[None]` can't satisfy with no payload.
def poll_ready_none() -> Own[Poll[None]]:
    return Poll[None].ready(None)


# Synchronous one-step driver. Calls `__poll__(Waker())` once and
# returns the Poll[T] for the caller to inspect. Useful for tests and
# synchronous drivers that don't go through `asyncio.run`.
def poll_once[T](aw: Awaitable[T]) -> Own[Poll[T]]:
    return aw.__poll__(Waker())


# Poll an awaitable once and report whether it raised CancelledError.
# Test convenience: production code should `try: await aw / except
# CancelledError` directly. Pure TPy body.
def task_poll_cancelled[T](aw: Awaitable[T]) -> bool:
    try:
        aw.__poll__(Waker())
        return False
    except CancelledError:
        return True
