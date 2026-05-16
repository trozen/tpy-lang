# tpy: cpp_namespace("tpystd::asyncio")
"""asyncio v1 -- minimum viable async runtime.

`run` / `sleep` / `create_task` / `Task[T]` / `Future[T]` / `Event` /
`CancelledError`. Lowers to `runtime/cpp/include/tpy/async.hpp` and
the TPy Executor in `_executor.py`. See `docs/ASYNC_DESIGN.md`.
"""
from builtins import BaseException, Exception
from tpy.extern import cpp_template, native
from tpy import Own, Int32, CancelledError
from tpy.coro import (
    Waker, Poll, Awaitable,
    poll_ready, poll_pending, poll_ready_none,
)
from tpy.mem import UninitArrayStorage
from tplib import Box
from time import monotonic
from ._executor import (
    Task, AnyTask, AsyncFrame,
    task_from_coro, make_executor_owned_task, task_to_any_box,
    Executor, ExecutorHandle, _ExecutorScope,
    _get_current_executor, _executor_spawn_via_handle,
)


def run[T](coro: Own[Awaitable[T]]) -> T:
    if not _get_current_executor().is_null():
        raise RuntimeError(
            "asyncio.run() cannot be called from a running event loop")
    task = make_executor_owned_task[T](coro)
    box = task_to_any_box[T](task)
    _run_drain_main_task(box)
    return task.__poll__(Waker()).value()


# Lives here (not _executor.py): C++ function using-decls in
# asyncio.hpp require the source namespace already opened, which
# breaks under the parent-package include cycle.
def _run_drain_main_task(box: Own[Box[AnyTask]]) -> None:
    executor = Executor()
    scope = _ExecutorScope(executor)
    main_id = executor.spawn(box)
    try:
        executor.run_until(main_id)
    finally:
        # Swallow drain-time exceptions; v1 has no place to surface them.
        try:
            executor.drain_spawned_with_cancel(main_id)
        except BaseException:
            pass


# Bridge: register a steady-clock-seconds deadline with the running
# executor. No-op if no executor is running (so hand-rolled awaitables
# polled from a test harness without `asyncio.run` don't crash).
@native("tpy::executor_register_timer_seconds")
def _register_timer_at(deadline_seconds: float, waker: Waker) -> None: ...


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

    # Required for structural conformance to `@dynamic AsyncFrame[T]`
    # (in `asyncio._executor`). Mirrors the codegen-emitted `cancel()` on
    # every generated coro struct.
    def cancel(self) -> None:
        self.__cancel_pending = True

    def __poll__(self, waker: Waker) -> Poll[None]:
        if self.__cancel_pending:
            self.__cancel_pending = False
            # TODO: cancelling a registered SleepFuture leaves its timer
            # entry in the executor's timer_heap + _timer_wakers. The
            # generation guard makes the eventual wake a silent no-op,
            # so it's harmless per-cancel, but the heap grows unbounded
            # under cancel-heavy workloads. Fix: track the (timer_id,
            # waker) pair on self at registration time and remove it
            # from the executor here before throwing. Needs an executor
            # `cancel_timer(timer_id)` primitive.
            raise CancelledError()
        if monotonic() >= self.deadline:
            return poll_ready_none()
        if not self.registered:
            _register_timer_at(self.deadline, waker)
            self.registered = True
        return poll_pending()


# Park the calling coroutine for `seconds` seconds. Returns an
# Own[Task[None]] whose underlying SleepFuture registers a timer with
# the current executor on first poll; the run loop wakes when the
# deadline arrives.
def sleep(seconds: float) -> Own[Task[None]]:
    return task_from_coro[None](SleepFuture(seconds))


# Spawn `coro` on the current executor and return an Own[Task[T]]
# handle. The task is registered with the executor's slot table so
# it runs concurrently with the spawning task. Raises RuntimeError if
# no event loop is running.
def create_task[T](coro: Own[Awaitable[T]]) -> Own[Task[T]]:
    handle = _get_current_executor()
    if handle.is_null():
        raise RuntimeError(
            "asyncio.create_task: no running event loop "
            "(call asyncio.run(coro) to drive it)")
    task = make_executor_owned_task[T](coro)
    box = task_to_any_box[T](task)
    _spawn_on_handle(handle, box)
    return task


# Non-template wrapper so the cpp_template's static_cast instantiates
# here rather than inside generic `create_task<T>` (where C++ template
# name-lookup trips on the fully qualified Executor namespace).
def _spawn_on_handle(handle: ExecutorHandle, box: Own[Box[AnyTask]]) -> Int32:
    return _executor_spawn_via_handle(handle, box)


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

    # Required for structural conformance to `@dynamic AsyncFrame[T]`
    # (in `asyncio._executor`). Future cancellation is task-level: the
    # awaiting Task throws CancelledError before re-polling the Future,
    # so the Future itself has no inner state to flip. This is the
    # protocol hook called via the type-erased Adapter; the body is
    # intentionally a no-op.
    def cancel(self) -> None:
        pass

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
        return poll_pending()


class Event:
    """Boolean completion signal -- the no-payload analog of Future.
    Single-awaiter v1.

    `set` / `clear` / `is_set` match CPython. Divergence: TPy's Event
    is directly awaitable (`await event`) where CPython requires
    `await event.wait()` -- TPy classes can't yet define `async def`
    methods, so tests using Event need `no_cpython.txt`.
    """

    _is_set: bool
    _has_waiter: bool
    _waiter: Waker

    def __init__(self) -> None:
        self._is_set = False
        self._has_waiter = False
        self._waiter = Waker()

    def is_set(self) -> bool:
        return self._is_set

    def set(self) -> None:
        if self._is_set:
            return
        self._is_set = True
        if self._has_waiter:
            self._waiter.wake()
            self._has_waiter = False

    def clear(self) -> None:
        self._is_set = False

    # Required for structural conformance to `@dynamic AsyncFrame[T]`
    # (in `asyncio._executor`). Event cancellation is task-level (see
    # Future.cancel above for the rationale); body is a no-op.
    def cancel(self) -> None:
        pass

    def __poll__(self, waker: Waker) -> Poll[None]:
        if self._is_set:
            return poll_ready_none()
        if self._has_waiter:
            raise ValueError(
                "Event already has a waiter (single-awaiter v1)")
        self._waiter = waker
        self._has_waiter = True
        return poll_pending()
