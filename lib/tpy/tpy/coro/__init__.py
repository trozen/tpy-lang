# tpy: cpp_namespace("tpystd::coro")
"""Coroutine / async runtime primitives.

Module name borrowed from the design-doc's `Coroutine[T]` terminology
(see Rust `std::task` / Tokio `tokio::task` for prior art).
"""
from .._typing import Protocol
from .._bootstrap._decorators import Own, dynamic
from .._bootstrap._extern import builtin_type
from .._builtins._exceptions import CancelledError
from .._core import Poll, Ptr, ValueType, int32


# The dispatch target for `Waker.wake()`. `asyncio.Executor` inherits
# this protocol; Waker holds a `Ptr[Awaker]` to the running executor and
# calls `mark_runnable` through the @dynamic vtable. The only concrete
# implementer today lives in `asyncio._executor`.
#
# `register_timer(deadline, Waker)` would form an `Awaker` <-> `Waker`
# forward-declaration cycle in generated C++ headers if it lived on
# this protocol; timer registration goes through a direct
# `Executor.register_timer(...)` call in `asyncio/__init__.py` where
# the concrete `Ptr[Executor]` handle is available.
@dynamic
class Awaker(Protocol):
    def mark_runnable(self, task_id: int32, generation: int32) -> None: ...


@builtin_type("tpy.coro.Waker")
class Waker(ValueType):
    """Handle that lets a parked task be re-scheduled.

    Awaitables that haven't yet produced a value store the Waker passed
    to their poll() method; when the underlying event fires, they call
    waker.wake() to signal the executor that the parked task is runnable.

    Layout: a non-owning `Ptr[Awaker]` to the running executor + a
    (task_id, generation) slot identifier. Late wakes (from a task that
    completed before wake() fires) are filtered by the executor's
    generation check.
    """
    awaker: Ptr[Awaker]
    task_id: int32
    generation: int32

    def __init__(self, awaker: Ptr[Awaker] = None, task_id: int32 = 0,
                 generation: int32 = 0) -> None:
        self.awaker = awaker
        self.task_id = task_id
        self.generation = generation

    # Not @readonly: wake() doesn't mutate self, but it dispatches into
    # the awaker's `mark_runnable`, which mutates the executor's runnable
    # queue. Marking wake() readonly would narrow `self.awaker` to
    # `Ptr[readonly[Awaker]]` and reject the call.
    def wake(self) -> None:
        if self.awaker is None:
            return
        try:
            self.awaker.mark_runnable(self.task_id, self.generation)
        except BaseException:
            # Swallow: mark_runnable can raise (e.g. OOM in the
            # runnable-queue push) and wake() has no useful error
            # channel. Use-after-free against a torn-down executor is
            # the caller's invariant -- see `_ExecutorScope` in
            # `lib/tpy/asyncio/_executor.py`.
            pass


# Structural awaitable. Distinct from `typing.Awaitable[T]` (CPython's
# `__await__`-based shape) -- TPy uses `__poll__(Waker) -> Poll[T]`.
# The dunder name matches how other TPy/typing structural protocols
# spell their required methods (`__iter__`, `__hash__`, `__lt__`, ...)
# and signals "runtime protocol method -- prefer `await` / `poll_once`
# to direct calls".
class Awaitable[T](Protocol):
    def __poll__(self, waker: Waker) -> Own[Poll[T]]: ...


# Cancellable awaitable -- the @dynamic protocol every consumer of
# asyncio's cancellation machinery accepts (`run`, `create_task`,
# `wait_for`, `Task[T]` storage, ...). Structurally extends `Awaitable`
# with a `cancel()` precondition that lets the task layer deliver a
# `CancelledError` at the awaitee's next suspension. gen_async.py
# auto-emits cancel() on every coro struct, so any compiled `async def`
# conforms automatically. Re-exported from `asyncio` for user-facing
# API typing.
@dynamic
class Cancellable[T](Protocol):
    def __poll__(self, waker: Waker) -> Own[Poll[T]]: ...
    def cancel(self) -> None: ...


# `AnyTask` (@dynamic protocol used by the task machinery) lives in
# `asyncio._executor` -- it sits on tplib, which isn't reachable from
# this implicit-stdlib module.


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
