# tpy: cpp_namespace("tpystd::asyncio")
"""asyncio v1 -- minimum viable async runtime.

`run` / `sleep` / `create_task` / `Task[T]` / `Future[T]` / `Event` /
`Lock` / `Semaphore` / `CancelledError`. Lowers to
`runtime/cpp/include/tpy/async.hpp` and the TPy Executor in
`_executor.py`. See `docs/ASYNC_DESIGN.md`.
"""
from builtins import BaseException, Exception, TimeoutError
from tpy import Own, Int32, Ptr, CancelledError, Throwable, nocopy
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
# Two homogeneous entrypoints share the same `_GatherFuture[T]` engine:
#  - `gather(*tasks)` -- variadic-positional form.
#  - `gather_list(tasks)` -- list-shaped form.
# Both require all tasks to share return type `T`. The CPython-shape
# heterogeneous variadic form `gather[*Ts](*coros) -> tuple[*Ts]`
# remains deferred (needs variadic generics + the async-def `*args`
# codegen fix; see TODO.md / BUGS.md).
async def gather_list[T](tasks: list[Task[T]]) -> list[T]:
    return await _GatherFuture[T](tasks)


def gather[T](*tasks: Task[T]) -> Own[_GatherFuture[T]]:
    # Sync factory returning the awaitable as Own[...] -- same shape as
    # the hand-written `_WaitForFuture(coro, timeout)` / `SleepFuture(t)`
    # awaitables, not the executor-registered `create_task` (gather does
    # not spawn on the executor; the caller awaits the returned future
    # directly). Each Task is Rc-cloned into an owned list so the
    # awaitable is self-contained across the await point. _GatherFuture
    # also re-clones internally; both bumps are O(1) refcount-only,
    # kept for code simplicity.
    #
    # The explicit `for ... append` loop (not a list comprehension) is
    # deliberate: iterating `tpy::varargs<Task[T]>` (non-value generic
    # vararg) goes through codegen surface that BUGS.md notes as
    # fragile in the comp/slicing form. Keep this shape.
    task_list: list[Task[T]] = []
    for t in tasks:
        task_list.append(t.clone())
    return _GatherFuture[T](task_list)


@nocopy
class Settled[T]:
    """One slot in `gather_list_settled`'s output. Exactly one of
    `value` / `exception` is populated; the other is `None`. The
    record shape sidesteps two TPy/CPython mismatches around the
    natural `T | BaseException` form: (1) TPy lowers union elements
    in containers to a value-variant, and the exception root is a
    polymorphic owner (slicing risk on by-value moves); (2) TPy
    `isinstance(x, Box[Throwable])` against a union member of
    that exact shape is not yet supported. Field access is a clean
    discriminator on either side.

    Callers inspect:
        if entry.exception is not None:
            raise entry.exception        # recover dynamic type via except
        elif entry.value is not None:
            handle_ok(entry.value.get())

    Both fields are wrapped in `Box` so the record stays trivially
    movable inside `list[Settled[T]]` regardless of T's value/non-value
    shape. The exception is stored as `Box[Throwable]` (the polymorphic
    root); re-raise it to recover the concrete subclass.
    """

    value: Box[T] | None
    exception: Box[Throwable] | None

    # Users normally don't construct Settled directly --
    # `gather_list_settled` is the only producer and assigns the
    # `value` / `exception` fields directly post-init.
    def __init__(self) -> None:
        self.value = None
        self.exception = None


@nocopy
class _GatherSettledFuture[T]:
    """Drives N already-spawned `Task[T]`s concurrently and harvests
    every result -- value OR exception -- into a `list[Settled[T]]`
    in input order. Variant of CPython
    `gather(*coros, return_exceptions=True)`.

    Unlike `_GatherFuture`, a sub-task exception does NOT cancel its
    siblings: each task runs to completion and contributes either its
    value or its raised exception to the result list. A sub-task that
    is cancelled independently (via its own handle) raises
    `CancelledError`, which is collected as a `Settled` entry like any
    other exception -- matching CPython's `return_exceptions=True`
    rule that a cancelled submitted task is treated as having raised.

    Cancelling the gather itself (the task awaiting it) is different:
    `cancel()` propagates cancel to every still-unsettled sub-task
    (so they don't leak), but the `CancelledError` then propagates UP
    to the awaiting caller -- it is NOT swallowed into the result
    list. This also matches CPython: cancelling `gather()` cancels
    it. (The collected-result path is therefore only observable for
    independently-cancelled sub-tasks, not for cancelling the gather
    caller.)

    Storage uses arrival-order parallel arrays for both values and
    exceptions (mirroring `_GatherFuture`'s shape). Boxes are
    `@nocopy`; the assembly loop ownership-transfers each entry out
    of its arrival array via `list.pop`, then assigns to the
    appropriate `Settled` field. Per-input-index storage is rejected
    here because `list[Box[T] | None]` element slots have no
    take-and-clear primitive that satisfies the borrow checker.
    """

    _tasks: list[Task[T]]
    _settled: list[bool]
    _result_indices: list[Int32]
    _result_boxes: list[Box[T]]
    _exc_indices: list[Int32]
    _exc_boxes: list[Box[Throwable]]
    _completed: Int32
    _cancel_pending: bool

    def __init__(self, tasks: list[Task[T]]) -> None:
        # `tasks` is borrowed from the caller -- Rc-clone each entry as
        # in `_GatherFuture` (avoids the named-local Own[list[T]] arg
        # codegen gap; each clone is one refcount bump on TaskState).
        self._tasks = []
        self._settled = []
        self._result_indices = []
        self._result_boxes = []
        self._exc_indices = []
        self._exc_boxes = []
        self._completed = 0
        self._cancel_pending = False
        for t in tasks:
            self._tasks.append(t.clone())
            self._settled.append(False)

    # Required for structural conformance to `@dynamic Cancellable[T]`.
    def cancel(self) -> None:
        self._cancel_pending = True

    def __poll__(self, waker: Waker) -> Own[Poll[list[Settled[T]]]]:
        n = len(self._tasks)
        if n == 0:
            empty: list[Settled[T]] = []
            return poll_ready(empty)

        # Outer cancel: mark every unsettled sub-task's slot runnable
        # via Task.cancel. Subs will raise CancelledError at their
        # next suspension; we collect it as an entry rather than
        # bubbling it out (CPython's return_exceptions semantics).
        was_canceling = self._cancel_pending
        self._cancel_pending = False
        if was_canceling:
            i: Int32 = 0
            while i < n:
                if not self._settled[i]:
                    self._tasks[i].cancel()
                i += 1

        i: Int32 = 0
        while i < n:
            if not self._settled[i]:
                try:
                    p = self._tasks[i].__poll__(waker)
                    if p.is_ready():
                        self._settled[i] = True
                        self._completed += 1
                        self._result_indices.append(i)
                        self._result_boxes.append(Box(p.value()))
                except BaseException as e:
                    self._settled[i] = True
                    self._completed += 1
                    self._exc_indices.append(i)
                    self._exc_boxes.append(Box(e.clone()))
            i += 1

        if self._completed < n:
            return poll_pending()

        # Assemble in input order. For each input slot, scan the
        # arrival-order arrays for a matching index, then `pop` to
        # transfer ownership of the Box out of the list. O(n^2)
        # walk -- N is typically small (handful of concurrent
        # operations); fine for v1.5.
        result: list[Settled[T]] = []
        orig_i: Int32 = 0
        while orig_i < n:
            settled_via_value = False
            k: Int32 = 0
            kn = len(self._result_indices)
            while k < kn:
                if self._result_indices[k] == orig_i:
                    self._result_indices.pop(k)
                    box = self._result_boxes.pop(k)
                    entry = Settled[T]()
                    entry.value = box
                    result.append(entry)
                    settled_via_value = True
                    break
                k += 1
            if not settled_via_value:
                k = 0
                kn = len(self._exc_indices)
                while k < kn:
                    if self._exc_indices[k] == orig_i:
                        self._exc_indices.pop(k)
                        ebox = self._exc_boxes.pop(k)
                        entry = Settled[T]()
                        entry.exception = ebox
                        result.append(entry)
                        break
                    k += 1
            orig_i += 1
        return poll_ready(result)


# Run `tasks` concurrently and harvest every result -- value OR
# exception -- into `list[Settled[T]]` in input order. Variant of
# CPython `asyncio.gather(*coros, return_exceptions=True)`: a sub-task
# failure does NOT cancel siblings (each runs to completion); a
# sub-task cancelled independently is collected as a `Settled` entry
# with `exception` populated (its `CancelledError`). Cancelling the
# gather caller itself propagates cancel into the sub-tasks for
# cleanup and then re-raises `CancelledError` to the caller -- it is
# NOT swallowed into the result list (matching CPython: cancelling
# gather() cancels it).
#
# TPy-specific shape: returns `list[Settled[T]]` rather than CPython's
# `list[T | BaseException]`. Two reasons: (1) TPy lowers union
# elements in containers to a value-variant and the exception root is
# a polymorphic owner (slicing risk); (2) `isinstance` against
# `Box[Throwable]` as a union member isn't yet supported. The
# `Settled[T]` record gives clean field-based discrimination; the
# exception is a `Box[Throwable]` (re-raise to recover the subclass).
#
# Sibling of `gather_list` / `gather` (cancel-and-re-raise variants).
async def gather_list_settled[T](
        tasks: list[Task[T]]) -> list[Settled[T]]:
    return await _GatherSettledFuture[T](tasks)


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

    `set` / `clear` / `is_set` / `wait` match CPython. TPy also allows
    awaiting the Event directly (`await event`) as a shorthand for
    `await event.wait()`.
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

    async def wait(self) -> bool:
        """Block until set; returns True (CPython parity). `await event`
        is the TPy shorthand."""
        await self
        return True

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


# A stale-generation wake (the front waiter's task since cancelled or
# completed) is swallowed by the Waker's own generation guard, so the
# resource it would have claimed just waits for the next signal -- a
# documented v1 cancel-while-parked gap, not a correctness issue here.
def _wake_one(waiters: list[Waker]) -> None:
    if len(waiters) > 0:
        w = waiters.pop(0)
        w.wake()


@nocopy
class Lock:
    """Mutual-exclusion lock for single-threaded async code.

    `acquire` / `release` / `locked` match CPython; use as an async
    context manager (`async with lock:`). Contending acquirers park in
    FIFO order and are woken one at a time on release.

    Not awaitable directly (`await lock` is rejected, matching CPython):
    acquisition goes through `acquire`, which awaits a private
    `_LockAcquire` holding a Ptr to the lock, so the lock itself never
    exposes `__poll__`.

    Fairness: a freshly-arriving acquirer can claim a just-released lock
    ahead of an already-parked waiter (the woken waiter re-checks and
    re-parks). Safe -- every poll re-validates `_locked` -- but not
    strictly FIFO-fair under heavy contention; benign single-threaded.
    """

    _locked: bool
    _waiters: list[Waker]

    def __init__(self) -> None:
        self._locked = False
        self._waiters = []

    def locked(self) -> bool:
        return self._locked

    async def acquire(self) -> bool:
        await _LockAcquire(self)
        return True

    def release(self) -> None:
        if not self._locked:
            raise RuntimeError("Lock is not acquired.")
        self._locked = False
        _wake_one(self._waiters)

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(self, exc_type: None, exc_val: None,
                        exc_tb: None) -> None:
        self.release()

    # Called by `_LockAcquire.__poll__` through a `Ptr[Lock]`: grab the
    # lock if free, else park `waker` in FIFO order. Returns True iff
    # acquired this poll.
    def _try_acquire(self, waker: Waker) -> bool:
        if not self._locked:
            self._locked = True
            return True
        self._waiters.append(waker)
        return False


@nocopy
class _LockAcquire:
    """Private awaitable backing `Lock.acquire`. Holds a `Ptr[Lock]` (the
    lock outlives the in-flight acquire) rather than making the lock its
    own awaitable, so `await lock` stays rejected like CPython.
    """

    _lock: Ptr[Lock]

    def __init__(self, lock: Ptr[Lock]) -> None:
        self._lock = lock

    # Task-level cancellation (the parked acquirer throws at its
    # suspension); no inner state to flip.
    def cancel(self) -> None:
        pass

    def __poll__(self, waker: Waker) -> Own[Poll[None]]:
        if self._lock._try_acquire(waker):
            return poll_ready_none()
        return poll_pending()


@nocopy
class Semaphore:
    """Counting semaphore for single-threaded async code.

    `acquire` decrements the internal counter, parking (FIFO) when it
    would go below zero; `release` increments it and wakes one waiter.
    `locked` reports whether the counter is exhausted. Matches CPython.
    Usable as an async context manager; not awaitable directly (see
    `Lock`). Same fairness note as `Lock`.
    """

    _value: Int32
    _waiters: list[Waker]

    def __init__(self, value: Int32 = 1) -> None:
        if value < 0:
            raise ValueError("Semaphore initial value must be >= 0")
        self._value = value
        self._waiters = []

    def locked(self) -> bool:
        # CPython also reports locked while acquirers are parked, not only
        # when the count hits 0. `_waiters` may retain a since-cancelled
        # waker (the cancel-while-parked gap), so this can over-report.
        return self._value == 0 or len(self._waiters) > 0

    async def acquire(self) -> bool:
        await _SemAcquire(self)
        return True

    def release(self) -> None:
        self._value += 1
        _wake_one(self._waiters)

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(self, exc_type: None, exc_val: None,
                        exc_tb: None) -> None:
        self.release()

    # See `Lock._try_acquire`: take a permit if available, else park.
    def _try_acquire(self, waker: Waker) -> bool:
        if self._value > 0:
            self._value -= 1
            return True
        self._waiters.append(waker)
        return False


@nocopy
class _SemAcquire:
    """Private awaitable backing `Semaphore.acquire` (see `_LockAcquire`)."""

    _sem: Ptr[Semaphore]

    def __init__(self, sem: Ptr[Semaphore]) -> None:
        self._sem = sem

    def cancel(self) -> None:
        pass

    def __poll__(self, waker: Waker) -> Own[Poll[None]]:
        if self._sem._try_acquire(waker):
            return poll_ready_none()
        return poll_pending()
