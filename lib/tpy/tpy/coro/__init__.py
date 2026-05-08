# tpy: cpp_namespace("tpystd::coro")
"""Coroutine / async runtime primitives.

Module name borrowed from the design-doc's `Coroutine[T]` terminology
(see Rust `std::task` / Tokio `tokio::task` for prior art).
"""
from .._typing import Protocol
from .._bootstrap._decorators import Own
from .._bootstrap._extern import cpp_template
from .._core import Task, Waker, Poll


# Structural awaitable. Distinct from `typing.Awaitable[T]` (CPython's
# `__await__`-based shape) -- TPy uses poll(Waker) -> Poll[T].
class Awaitable[T](Protocol):
    def poll(self, waker: Waker) -> Poll[T]: ...


@cpp_template("::tpy::Poll<{T}>::ready({0})")
def poll_ready[T](value: Own[T]) -> Poll[T]: ...


@cpp_template("::tpy::Poll<{T}>::pending()")
def poll_pending[T]() -> Poll[T]: ...


# Separate factory because `Poll<void>::ready()` takes no value, so
# the generic `poll_ready[T](value)` shape doesn't apply when T is None.
@cpp_template("::tpy::Poll<void>::ready()")
def poll_ready_none() -> Poll[None]: ...
