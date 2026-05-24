# tpy: cpp_namespace("tpystd::asyncio")
"""asyncio v1 -- minimum viable async runtime.

`run` / `sleep` / `create_task` / `Task[T]` / `Future[T]` / `Event` /
`CancelledError`. Lowers to `runtime/cpp/include/tpy/async.hpp` and
the TPy Executor in `_executor.py`. See `docs/ASYNC_DESIGN.md`.
"""
from builtins import BaseException, Exception, TimeoutError
from tpy import Own, Int32, CancelledError, Throwable, nocopy
from tpy.coro import (
    Waker, Poll, Cancellable,
    poll_ready, poll_pending, poll_ready_none,
)
from tpy.mem import UninitArrayStorage
from tplib import Box
from time import monotonic
from ._executor import (
    Task, AnyTask,
    task_from_coro, make_executor_owned_task, task_to_any_box,
    Executor, _ExecutorScope,
    _get_current_executor,
)


def run[T](coro: Own[Cancellable[T]]) -> T:
    if _get_current_executor() is not None:
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


# No-op if no executor is running, so hand-rolled awaitables polled
# from a test harness without `asyncio.run` don't crash.
def _register_timer_at(deadline_seconds: float, waker: Waker) -> None:
    handle = _get_current_executor()
    if handle is None:
        return
    handle.register_timer(deadline_seconds, waker)


class SleepFuture:
    """Awaits a steady-clock deadline. Registers a timer with the
    current executor on first poll and returns Pending until the
    deadline elapses; conforms to the Awaitable[None] shape (poll +
    `_cancel_pending` field that the runtime's `Task::cancel` flips).
    """

    deadline: float
    registered: bool
    _cancel_pending: bool

    def __init__(self, seconds: float) -> None:
        self.deadline = monotonic() + seconds
        self.registered = False
        self._cancel_pending = False

    # Required for structural conformance to `@dynamic Cancellable[T]`
    # (in `tpy.coro`). Mirrors the codegen-emitted `cancel()` on
    # every generated coro struct.
    def cancel(self) -> None:
        self._cancel_pending = True

    def __poll__(self, waker: Waker) -> Own[Poll[None]]:
        if self._cancel_pending:
            self._cancel_pending = False
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
def create_task[T](coro: Own[Cancellable[T]]) -> Own[Task[T]]:
    handle = _get_current_executor()
    if handle is None:
        raise RuntimeError(
            "asyncio.create_task: no running event loop "
            "(call asyncio.run(coro) to drive it)")
    task = make_executor_owned_task[T](coro)
    box = task_to_any_box[T](task)
    slot_id = handle.spawn(box)
    # Stamp a Waker for the slot onto the Task so `Task.cancel()` can
    # mark the slot runnable promptly. Read the freshly-spawned slot's
    # generation directly rather than hardcoding 0, so this stays
    # correct if `Slot.__init__` ever changes its starting generation
    # (e.g. for slot recycling). If the slot completes (generation
    # bumps) before cancel fires, Waker.wake's generation guard
    # silently drops the wake. Non-executor-owned tasks (built via
    # `task_from_coro`) keep the default null-awaker Waker assigned in
    # Task.__init__, and wake() is a safe no-op on those.
    task._waker = handle.make_waker_for_slot(
        slot_id, handle.slots[slot_id].generation)
    return task


@nocopy
class _WaitForFuture[T]:
    """Drives an inner Cancellable[T] against a steady-clock deadline.

    Registers a one-shot timer with the current executor on first
    poll. When the deadline elapses, propagates `cancel()` to the
    inner frame and keeps polling it until it returns -- translating
    the resulting `CancelledError` into `TimeoutError`. An external
    cancel (outer task cancellation) propagates to the inner unchanged
    and re-raises `CancelledError`. The deadline timer can fire after
    the inner already completed; the generation guard in
    `Executor.mark_runnable` swallows the late wake.
    """

    _inner: Box[Cancellable[T]]
    _deadline: float
    _registered: bool
    _cleanup: bool
    _timed_out: bool
    _cancel_pending: bool

    def __init__(self, coro: Own[Cancellable[T]], timeout: float) -> None:
        self._inner = Box[Cancellable[T]](coro)
        self._deadline = monotonic() + timeout
        self._registered = False
        self._cleanup = False
        self._timed_out = False
        self._cancel_pending = False

    # Required for `@dynamic Cancellable[T]` conformance. Flips the
    # cancel flag; the next __poll__ propagates to the inner. Matches
    # the SleepFuture / Future / Event pattern.
    def cancel(self) -> None:
        self._cancel_pending = True

    def __poll__(self, waker: Waker) -> Own[Poll[T]]:
        # Consume the cancel signal up front so a re-cancel arriving
        # after `_cleanup=True` doesn't accumulate (cleanup is already
        # in flight; the second cancel adds nothing). Mirrors the
        # "consumed each poll" invariant SleepFuture maintains.
        was_canceling = self._cancel_pending
        self._cancel_pending = False

        # Outer cancel wins over a pending timeout. Mark cleanup so
        # an in-flight inner cancel observed below re-raises as
        # CancelledError, not TimeoutError.
        if was_canceling and not self._cleanup:
            self._cleanup = True
            self._inner.get().cancel()

        if not self._registered:
            _register_timer_at(self._deadline, waker)
            self._registered = True

        # Deadline elapsed and we have not started cleanup yet:
        # propagate cancel to the inner and pump it until it returns.
        # A non-positive timeout takes this branch on the first poll.
        if not self._cleanup and monotonic() >= self._deadline:
            self._cleanup = True
            self._timed_out = True
            self._inner.get().cancel()

        try:
            return self._inner.get().__poll__(waker)
        except CancelledError:
            if self._timed_out:
                raise TimeoutError()
            raise


# Await `coro` with a steady-clock deadline of `timeout` seconds.
# Returns the coroutine's value if it completes before the deadline.
# Otherwise propagates `cancel()` to the coroutine, pumps it until it
# observes the cancellation, then raises `TimeoutError`. A non-
# positive `timeout` triggers the deadline on the first poll (matches
# CPython).
#
# Outer cancellation of a task awaiting `wait_for` propagates through
# to the inner coroutine: the resume-case cancel-check (in every
# async-def coro frame) calls `cancel()` on the in-flight sub-coro
# before polling, so the inner observes `CancelledError` at its
# suspension point and can run `finally`-with-await cleanup before
# the cancellation surfaces to the caller.
async def wait_for[T](coro: Own[Cancellable[T]], timeout: float) -> T:
    return await _WaitForFuture[T](coro, timeout)


@nocopy
class _GatherFuture[T]:
    """Drives N already-spawned `Task[T]`s concurrently and harvests
    their results in input order.

    Each `__poll__` cycle polls every still-unsettled task with the
    awaiter's waker. When a sub-task completes, its TaskState wakes
    that waker, scheduling another gather poll. On the first failure
    (or outer cancel), transitions to cleanup mode: propagates
    `cancel()` to remaining sub-tasks and discards their values as
    they settle. Re-raises the first exception once every sub-task
    has settled.

    Cancel propagation is prompt for `create_task`-built sub-tasks:
    `Task.cancel()` (since M10) fires a per-Task `Waker` stamped at
    `create_task` time, marking the slot runnable so the in-flight
    frame observes its `__cancel_pending` on its next poll rather
    than waiting on a natural wake. Sub-tasks built via
    `task_from_coro` (not registered with the executor) carry the
    default null-awaker Waker, so cancel-wake is a no-op for them and
    observation reverts to the sub-task's next natural progress --
    the same latency that pre-M10 gather had.
    """

    _tasks: list[Task[T]]
    _completion_indices: list[Int32]
    _completion_boxes: list[Box[T]]
    _settled: list[bool]
    _exc: Box[Throwable] | None
    _completed: Int32
    _cleanup: bool
    _cancel_pending: bool

    def __init__(self, tasks: list[Task[T]]) -> None:
        # tasks is borrowed from the caller. Rc-clone each Task into
        # our owned list so the future is self-contained -- avoids the
        # codegen gap on passing a named-local Own[list[T]] arg into a
        # sub-coro emplace (cheap: each clone is one refcount bump on
        # the underlying TaskState).
        self._tasks = []
        self._completion_indices = []
        self._completion_boxes = []
        self._settled = []
        self._exc = None
        self._completed = 0
        self._cleanup = False
        self._cancel_pending = False
        for t in tasks:
            self._tasks.append(t.clone())
            self._settled.append(False)

    # Required for structural conformance to `@dynamic Cancellable[T]`.
    # Mirrors the SleepFuture / _WaitForFuture pattern: just flips the
    # signal; the next __poll__ propagates to sub-tasks.
    def cancel(self) -> None:
        self._cancel_pending = True

    def __poll__(self, waker: Waker) -> Own[Poll[list[T]]]:
        # Empty gather is trivially complete on every poll, including
        # the first one after an outer cancel. Matches CPython's
        # "gather() with no args returns []".
        n = len(self._tasks)
        if n == 0:
            empty: list[T] = []
            return poll_ready(empty)

        # Consume cancel signal up front so a re-cancel arriving after
        # cleanup is already in flight doesn't accumulate (mirrors
        # _WaitForFuture). Propagate cancel into sub-tasks now; defer
        # claiming `_exc` until AFTER the per-task loop so a freshly
        # observable inner exception this cycle wins over the cancel
        # (avoids silently dropping a real failure under racing-cancel).
        was_canceling = self._cancel_pending
        self._cancel_pending = False
        if was_canceling and not self._cleanup:
            self._cleanup = True
            self._propagate_cancel()

        # Poll every unsettled task with the shared waker. Multiple
        # wakes between polls coalesce at the executor (one runnable
        # flag per slot), so the O(N) re-poll per cycle is bounded.
        i: Int32 = 0
        while i < n:
            if not self._settled[i]:
                try:
                    p = self._tasks[i].__poll__(waker)
                    if p.is_ready():
                        self._settled[i] = True
                        self._completed += 1
                        if self._cleanup:
                            # Cleanup mode: discard value; `p` drops
                            # at end of block and the destructor
                            # disposes the contained T.
                            pass
                        else:
                            self._completion_indices.append(i)
                            self._completion_boxes.append(Box(p.value()))
                except BaseException as e:
                    self._settled[i] = True
                    self._completed += 1
                    if self._exc is None:
                        self._exc = Box(e.clone())
                    if not self._cleanup:
                        self._cleanup = True
                        self._propagate_cancel()
            i += 1

        # Outer-cancel fallback: only claim `_exc` for CancelledError
        # if nothing real fired this cycle (or any prior one).
        if was_canceling and self._exc is None:
            self._exc = Box(CancelledError())

        if self._completed < n:
            return poll_pending()

        if self._exc is not None:
            raise self._exc

        # All sub-tasks succeeded. Reorder completions into input
        # order. O(n^2) walk -- n is typically small (handful of
        # concurrent operations); fine for v1.5.
        result: list[T] = []
        orig_i: Int32 = 0
        while orig_i < n:
            k: Int32 = 0
            kn = len(self._completion_indices)
            while k < kn:
                if self._completion_indices[k] == orig_i:
                    box = self._completion_boxes.pop(k)
                    self._completion_indices.pop(k)
                    result.append(box.take())
                    break
                k += 1
            orig_i += 1
        return poll_ready(result)

    def _propagate_cancel(self) -> None:
        i: Int32 = 0
        n = len(self._tasks)
        while i < n:
            if not self._settled[i]:
                self._tasks[i].cancel()
            i += 1


# Run `tasks` concurrently and return their results in input order.
# On the first sub-task exception (or outer cancellation), propagates
# `cancel()` to the remaining sub-tasks, waits for them to settle, then
# re-raises the first exception encountered.
#
# TPy-specific helper -- not in CPython. The name `gather` is reserved
# for the future CPython-compatible variadic-tuple form
# `gather(c1, c2, c3) -> tuple[T1, T2, T3]` once variadic generics land
# (TODO.md). Until then, `gather_list` is the homogeneous-only
# entrypoint: all tasks must share return type `T`.
async def gather_list[T](tasks: list[Task[T]]) -> list[T]:
    return await _GatherFuture[T](tasks)


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
    _exception: Box[Throwable] | None
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

    def set_exception(self, exc: Own[Throwable]) -> None:
        if self._done:
            raise InvalidStateError("Future already done")
        self._exception = Box(exc)
        self._done = True
        if self._has_waiter:
            self._waiter.wake()
            self._has_waiter = False

    # Required for structural conformance to `@dynamic Cancellable[T]`
    # (in `tpy.coro`). Future cancellation is task-level: the
    # awaiting Task throws CancelledError before re-polling the Future,
    # so the Future itself has no inner state to flip. This is the
    # protocol hook called via the type-erased Adapter; the body is
    # intentionally a no-op.
    def cancel(self) -> None:
        pass

    def __poll__(self, waker: Waker) -> Own[Poll[T]]:
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

    # Required for structural conformance to `@dynamic Cancellable[T]`
    # (in `tpy.coro`). Event cancellation is task-level (see
    # Future.cancel above for the rationale); body is a no-op.
    def cancel(self) -> None:
        pass

    def __poll__(self, waker: Waker) -> Own[Poll[None]]:
        if self._is_set:
            return poll_ready_none()
        if self._has_waiter:
            raise ValueError(
                "Event already has a waiter (single-awaiter v1)")
        self._waiter = waker
        self._has_waiter = True
        return poll_pending()
