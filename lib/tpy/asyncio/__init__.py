# tpy: cpp_namespace("tpystd::asyncio")
"""asyncio v1 -- minimum viable async runtime.

Provides:
  * `asyncio.run(coro)` -- drive a top-level coroutine to completion;
    sleeps idle on the executor's timer heap when the coro is Pending.
  * `asyncio.sleep(seconds)` -- park the calling coroutine for a
    duration. Lowered to a SleepFuture wrapped in Task[None] -- the
    user `await`s it like any Task.
  * `asyncio.Future[T]` -- single-awaiter manual-completion awaitable.

The CPython phase falls through to CPython's own `asyncio` module (this
file is not under `lib/cpy/`); on the TPy compile path the bindings
lower to helpers in `runtime/cpp/include/tpy/async.hpp`. `@native` is
used for non-template bridges; `@cpp_template` only where the C++ side
is a function template that needs per-call-site instantiation
(`async_run`, `Task<void>::from_coro`, `make_user_task`).
"""
from builtins import BaseException, Exception
from tpy.extern import cpp_template, native
from tpy import Own, CancelledError
from tpy.coro import (
    Task, Waker, Poll, Awaitable,
    poll_ready, poll_pending, poll_ready_none,
)
from tpy.mem import UninitArrayStorage
from time import monotonic


# Run a coroutine to completion. Drives the executor's timer heap.
# TODO: replace with native?
@cpp_template("::tpy::async_run({0})")
def run[T](coro: Awaitable[T]) -> T: ...


# Bridge: register a steady-clock-seconds deadline with the running
# executor. No-op if no executor is running (so hand-rolled awaitables
# polled from a test harness without `asyncio.run` don't crash).
@native("tpy::executor_register_timer_seconds")
def _register_timer_at(deadline_seconds: float, waker: Waker) -> None: ...


# Bridge: wrap a value-typed awaitable into a Task[None] via the
# runtime's heterogeneous poll-box. Used by `sleep` to ship a
# SleepFuture (a TPy class) through the executor's spawn list.
@cpp_template("::tpy::Task<void>::from_coro({0})")
def _task_void_from_coro[T](coro: T) -> Task[None]: ...


class SleepFuture:
    """Awaits a steady-clock deadline. Registers a timer with the
    current executor on first poll and returns Pending until the
    deadline elapses; conforms to the Awaitable[None] shape (poll +
    `__cancel_pending` field that the runtime's `Task::cancel` flips).
    """

    deadline: float
    registered: bool
    __cancel_pending: bool

    def __init__(self, seconds: float) -> None:
        self.deadline = monotonic() + seconds
        self.registered = False
        self.__cancel_pending = False

    def __poll__(self, waker: Waker) -> Poll[None]:
        if self.__cancel_pending:
            self.__cancel_pending = False
            raise CancelledError()
        if monotonic() >= self.deadline:
            return poll_ready_none()
        if not self.registered:
            _register_timer_at(self.deadline, waker)
            self.registered = True
        return poll_pending[None]()


# Park the calling coroutine for `seconds` seconds. Returns a Task[None]
# whose underlying SleepFuture registers a timer with the current
# executor on first poll; the run loop wakes when the deadline arrives.
def sleep(seconds: float) -> Task[None]:
    return _task_void_from_coro(SleepFuture(seconds))


# Spawn `coro` on the current executor and return a Task[T] handle.
# The C++ helper (`make_user_task`) panics if no event loop is running.
@cpp_template("::tpy::make_user_task<{T}>({0})")
def create_task[T](coro: Awaitable[T]) -> Task[T]: ...


class InvalidStateError(Exception):
    """Raised when set_result / set_exception is called on a Future
    that is already done."""
    pass


class Future[T]:
    """Single-awaiter, manual-completion awaitable.

    Construct, await it from a coroutine, then call `set_result(value)`
    (or `set_exception(exc)`) from another context to complete it.

    v1 limitations:
      - Single-awaiter: a second waiter raises ValueError.
      - poll() consumes the result: a second poll after Ready panics
        (or wakes a future poll on the empty storage). Multi-awaiter v2
        will keep the result around.
    """

    _done: bool
    _has_waiter: bool
    _has_result: bool
    _exception: BaseException | None
    _waiter: Waker
    _result: UninitArrayStorage[T, 1]

    def __init__(self) -> None:
        self._done = False
        self._has_waiter = False
        self._has_result = False
        self._exception = None
        self._waiter = Waker()
        self._result = UninitArrayStorage[T, 1]()

    def __del__(self) -> None:
        if self._has_result:
            self._result.take0()
            self._has_result = False

    def done(self) -> bool:
        return self._done

    def set_result(self, value: Own[T]) -> None:
        # Takes ownership of `value` so nocopy types work without a
        # `__copy__` opt-in. Callers passing a named local must use
        # `tpy.copy(x)` explicitly to keep their reference alive.
        if self._done:
            raise InvalidStateError("Future already done")
        self._result.init0(value)
        self._has_result = True
        self._done = True
        if self._has_waiter:
            self._waiter.wake()
            self._has_waiter = False

    def set_exception(self, exc: Own[BaseException]) -> None:
        if self._done:
            raise InvalidStateError("Future already done")
        self._exception = exc
        self._done = True
        if self._has_waiter:
            self._waiter.wake()
            self._has_waiter = False

    def __poll__(self, waker: Waker) -> Poll[T]:
        if self._done:
            if self._exception is not None:
                raise self._exception
            if not self._has_result:
                raise ValueError(
                    "Future done with no result and no exception")
            self._has_result = False
            # take0() returns Own[T] -- pass directly as rvalue so
            # nocopy types (T with __del__ but no __copy__) flow through
            # without requiring a copy ctor.
            return poll_ready(self._result.take0())
        if self._has_waiter:
            raise ValueError(
                "Future already has a waiter (single-awaiter v1)")
        self._waiter = waker
        self._has_waiter = True
        return poll_pending[T]()
