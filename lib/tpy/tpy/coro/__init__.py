# tpy: cpp_namespace("tpystd::coro")
"""Coroutine / async runtime primitives.

Module name borrowed from the design-doc's `Coroutine[T]` terminology
(see Rust `std::task` / Tokio `tokio::task` for prior art).
"""
from .._typing import Protocol
from .._bootstrap._decorators import Own
from .._bootstrap._extern import cpp_template
from .._builtins._exceptions import CancelledError
from .._core import Task, Waker, Poll


# Structural awaitable. Distinct from `typing.Awaitable[T]` (CPython's
# `__await__`-based shape) -- TPy uses `__poll__(Waker) -> Poll[T]`.
# The dunder name matches how other TPy/typing structural protocols
# spell their required methods (`__iter__`, `__hash__`, `__lt__`, ...)
# and signals "runtime protocol method -- prefer `await` / `poll_once`
# to direct calls".
class Awaitable[T](Protocol):
    def __poll__(self, waker: Waker) -> Poll[T]: ...


@cpp_template("::tpy::Poll<{T}>::ready({0})")
def poll_ready[T](value: Own[T]) -> Poll[T]: ...


@cpp_template("::tpy::Poll<{T}>::pending()")
def poll_pending[T]() -> Poll[T]: ...


# Separate factory because `Poll<void>::ready()` takes no value, so
# the generic `poll_ready[T](value)` shape doesn't apply when T is None.
@cpp_template("::tpy::Poll<void>::ready()")
def poll_ready_none() -> Poll[None]: ...


# Synchronous one-step driver. Calls `__poll__(Waker())` once and
# returns the Poll[T] for the caller to inspect. Useful for tests and
# synchronous drivers that don't go through `asyncio.run`.
def poll_once[T](aw: Awaitable[T]) -> Poll[T]:
    return aw.__poll__(Waker())


# Box an awaitable into a heap-allocated Task[T] without registering it
# with an executor (no `asyncio.run` required). Mirrors C++
# `::tpy::Task<T>::from_coro(c)`. Used by tests that drive a Task
# manually -- production code should reach for `asyncio.create_task`.
@cpp_template("::tpy::Task<{T}>::from_coro({0})")
def task_from_coro[T](coro: Awaitable[T]) -> Own[Task[T]]: ...


# Poll an awaitable once and report whether it raised CancelledError.
# Test convenience: production code should `try: await aw / except
# CancelledError` directly. Pure TPy body.
def task_poll_cancelled[T](aw: Awaitable[T]) -> bool:
    try:
        aw.__poll__(Waker())
        return False
    except CancelledError:
        return True
