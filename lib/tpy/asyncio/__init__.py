# tpy: cpp_namespace("tpystd::asyncio")
"""asyncio v1 -- minimum viable async runtime.

`run` / `sleep` / `create_task` / `Task[T]` / `Future[T]` / `Event` /
`Lock` / `Semaphore` / `BoundedSemaphore` / `Queue[T]` /
`CancelledError`. Lowers to
`runtime/cpp/include/tpy/async.hpp` and the TPy Executor in
`_executor.py`. See `docs/ASYNC_DESIGN.md`.
"""
from typing import Final, Callable
from builtins import BaseException, Exception, TimeoutError, EOFError
from tpy import (
    Own, int32, uint32, uint64, Ptr,
    CancelledError, Throwable, nocopy, auto_readonly,
)
from tpy.coro import (
    Waker, Poll, Cancellable,
    poll_ready, poll_pending, poll_ready_none,
)
from tpy.mem import UninitStorage
from tplib import Box
from tplib.rc import Rc
from time import monotonic
from socket import (
    socket, SocketError, SOL_SOCKET, SO_ERROR, SO_REUSEADDR, AF_INET,
    SOCK_STREAM, _maybe_raise_connection_error, _strerror)
from _bindings import posix_signal
from ._executor import (
    Task, AnyTask,
    task_from_coro, make_executor_owned_task, task_to_any_box,
    Executor, _ExecutorScope,
    _get_current_executor,
)


# `-> Own[T]`: the result is moved out of the task's owned result slot.
# A bare `-> T` would render the generic borrow convention
# (val_or_ref_t<T> = T& for object T), which cannot bind the slot's
# moved-out rvalue -- and a borrow could not outlive the executor anyway.
def run[T](coro: Own[Cancellable[T]]) -> Own[T]:
    if _get_current_executor() is not None:
        raise RuntimeError(
            "asyncio.run() cannot be called from a running event loop")
    task = make_executor_owned_task[T](coro)
    box = task_to_any_box[T](task)
    interrupted = _run_drain_main_task(box)
    try:
        return task.__poll__(Waker()).value()
    except CancelledError:
        # CPython parity: a consumed SIGINT surfaces as KeyboardInterrupt only
        # when the cancellation actually propagated out of the root. A root that
        # caught the CancelledError (returning a value) or raised a different
        # exception during cleanup keeps its own outcome via this normal path --
        # we must not swallow its result or its exception.
        if interrupted:
            raise KeyboardInterrupt()
        raise


# Lives here (not _executor.py): C++ function using-decls in
# asyncio.hpp require the source namespace already opened, which
# breaks under the parent-package include cycle.
def _run_drain_main_task(box: Own[Box[AnyTask]]) -> bool:
    executor = Executor()
    scope = _ExecutorScope(executor)
    # Declared after the executor scope so its __del__ (restore handlers) runs
    # before the executor/reactor teardown.
    signals = _SignalScope(executor)
    main_id = executor.spawn(box)
    interrupted = False
    completed = False
    try:
        interrupted = executor.run_until(main_id)
        completed = True
    finally:
        # A run left by an exception (a task's SystemExit, a second Ctrl-C)
        # cancels the root too, so its finally / __aexit__ run as in
        # CPython's Runner.close.
        skip_id = main_id if completed else -1
        try:
            executor.drain_spawned_with_cancel(skip_id)
        except (SystemExit, KeyboardInterrupt):
            # Raised by a task during cleanup: it replaces the exception in
            # flight, as in CPython.
            raise
        except BaseException:
            # Other drain-time exceptions have no place to surface in v1.
            pass
    return interrupted


# No-op if no executor is running, so hand-rolled awaitables polled
# from a test harness without `asyncio.run` don't crash.
def _register_timer_at(deadline_seconds: float, waker: Waker) -> None:
    handle = _get_current_executor()
    if handle is None:
        return
    handle.register_timer(deadline_seconds, waker)


# epoll interest masks (Linux-stable), passed to the reactor by the fd
# awaitables below. The EPOLL_CTL_* ops live in `_executor.py`.
EPOLLIN: Final[uint32] = uint32(0x001)
EPOLLOUT: Final[uint32] = uint32(0x004)


@nocopy
class _SignalScope:
    """RAII guard giving `asyncio.run` Ctrl-C delivery for its duration
    (SIGINT only, matching CPython; SIGTERM keeps its default).

    Takes delivery over from the process-wide SIGINT layer (synchronous check
    points stop raising KeyboardInterrupt while the run lasts) and registers
    the layer's wake fd in the executor's epoll set (no-op waker) so a signal
    wakes a blocked `epoll_wait`; `__del__` unregisters the fd and hands
    delivery back. A run that gets no fd (not on the interrupt target thread,
    SIGINT inherited as ignored, or a failed install) stays unarmed. Note: the
    registered fd keeps the reactor fd count >= 1, so the "no progress
    possible" deadlock guard stays quiet while armed (CPython has no such
    guard)."""

    _armed: bool
    _fd: int32

    def __init__(self, executor: Executor) -> None:
        self._armed = False
        self._fd = -1
        fd = posix_signal.async_begin()
        if fd >= 0:
            executor.register_fd(fd, EPOLLIN, Waker())
            executor.shutdown_armed = True
            executor.shutdown_fd = fd
            self._armed = True
            self._fd = fd

    def __del__(self) -> None:
        if self._armed:
            # Unregister before async_end() may close the fd, so the reactor's
            # waiter table is not left with a stale (closed) entry. The current
            # executor is still set here (this scope tears down before the
            # _ExecutorScope that clears it).
            _reactor_unregister_fd(self._fd)
            posix_signal.async_end()


# Reactor access mirrors `_register_timer_at`: no-op when no executor is
# running so a hand-driven awaitable doesn't crash outside `asyncio.run`.
def _reactor_register_fd(fd: int32, events: uint32, waker: Waker) -> None:
    handle = _get_current_executor()
    if handle is None:
        return
    handle.register_fd(fd, events, waker)


def _reactor_unregister_fd(fd: int32) -> None:
    handle = _get_current_executor()
    if handle is None:
        return
    handle.unregister_fd(fd)


# The socket awaitables are hand-written (like SleepFuture / _QueueWait),
# NOT `async def`s, for two reasons: each parks by returning Pending +
# arming the reactor (no nested await), and an `async def` taking a
# reference-type by-value param (`data: bytes`) hits a coro-frame
# storage-form codegen gap (see BUGS.md). `sock_recv` / `sock_sendall` /
# `sock_accept` / `sock_connect` are thin sync factories returning these
# awaitables, the same shape as `gather(...) -> Own[_GatherFuture]`.
#
# Each holds a `Ptr[socket]` (the socket outlives the in-flight op, owned
# by the caller across the await) and drives the public `socket` methods,
# parking on `BlockingIOError` -- mirroring CPython's `loop.sock_*`, which
# call the same public methods and catch EAGAIN/EINPROGRESS rather than
# reaching into socket internals.


@nocopy
class _SockRecv:
    """Awaitable backing `EventLoop.sock_recv`. Calls the public
    `socket.recv`; on `BlockingIOError` arms the reactor for EPOLLIN and
    parks, retrying on wake. Yields up to `_n` bytes (empty == peer
    closed)."""

    _sock: Ptr[socket]
    _n: int32
    _cancel_pending: bool

    def __init__(self, sock: Ptr[socket], n: int32) -> None:
        self._sock = sock
        self._n = n
        self._cancel_pending = False

    def cancel(self) -> None:
        self._cancel_pending = True

    def __poll__(self, waker: Waker) -> Own[Poll[bytes]]:
        if self._cancel_pending:
            self._cancel_pending = False
            # Drop the reactor arming if we parked (idempotent if we never
            # registered or the fd already fired) so the entry doesn't leak.
            _reactor_unregister_fd(self._sock.fileno())
            raise CancelledError()
        try:
            return poll_ready(self._sock.recv(self._n))
        except BlockingIOError:
            _reactor_register_fd(self._sock.fileno(), EPOLLIN, waker)
            return poll_pending()


@nocopy
class _SockSendAll:
    """Awaitable backing `EventLoop.sock_sendall`. Owns a copy of the data
    (it must outlive each park), advancing `_sent` across the public
    `socket.send`; on `BlockingIOError` arms the reactor for EPOLLOUT and
    parks."""

    _sock: Ptr[socket]
    _data: bytes
    _sent: uint64
    _cancel_pending: bool

    def __init__(self, sock: Ptr[socket], data: bytes) -> None:
        self._sock = sock
        self._data = data
        self._sent = 0
        self._cancel_pending = False

    def cancel(self) -> None:
        self._cancel_pending = True

    def __poll__(self, waker: Waker) -> Own[Poll[None]]:
        if self._cancel_pending:
            self._cancel_pending = False
            _reactor_unregister_fd(self._sock.fileno())
            raise CancelledError()
        total: uint64 = uint64(len(self._data))
        while self._sent < total:
            try:
                sent = self._sock._send_from(self._data, self._sent)
            except BlockingIOError:
                _reactor_register_fd(self._sock.fileno(), EPOLLOUT, waker)
                return poll_pending()
            self._sent = self._sent + uint64(sent)
        return poll_ready_none()


@nocopy
class _SockAccept:
    """Awaitable backing `EventLoop.sock_accept`. Calls
    `socket._accept_nonblocking` (accept + set the conn non-blocking, since
    accepted sockets do NOT inherit O_NONBLOCK on Linux); on
    `BlockingIOError` arms the reactor for EPOLLIN and parks. Yields
    `(conn, (host, port))`, matching CPython's `loop.sock_accept`."""

    _sock: Ptr[socket]
    _cancel_pending: bool

    def __init__(self, sock: Ptr[socket]) -> None:
        self._sock = sock
        self._cancel_pending = False

    def cancel(self) -> None:
        self._cancel_pending = True

    def __poll__(self, waker: Waker
                 ) -> Own[Poll[tuple[Own[socket], tuple[str, int32]]]]:
        if self._cancel_pending:
            self._cancel_pending = False
            _reactor_unregister_fd(self._sock.fileno())
            raise CancelledError()
        try:
            return poll_ready(self._sock._accept_nonblocking())
        except BlockingIOError:
            _reactor_register_fd(self._sock.fileno(), EPOLLIN, waker)
            return poll_pending()


@nocopy
class _SockConnect:
    """Awaitable backing `EventLoop.sock_connect`. First poll calls the
    public `socket.connect`; a non-blocking connect raises `BlockingIOError`
    (EINPROGRESS), so we arm the reactor for EPOLLOUT and park. On wake the
    connect has resolved; SO_ERROR distinguishes success (0) from failure.
    Mirrors CPython's `loop.sock_connect`."""

    _sock: Ptr[socket]
    _addr: tuple[str, int32]
    _started: bool
    _cancel_pending: bool

    def __init__(self, sock: Ptr[socket], addr: tuple[str, int32]) -> None:
        self._sock = sock
        self._addr = addr
        self._started = False
        self._cancel_pending = False

    def cancel(self) -> None:
        self._cancel_pending = True

    def __poll__(self, waker: Waker) -> Own[Poll[None]]:
        if self._cancel_pending:
            self._cancel_pending = False
            _reactor_unregister_fd(self._sock.fileno())
            raise CancelledError()
        if not self._started:
            self._started = True
            try:
                self._sock.connect(self._addr)
                # Connected synchronously (can happen on loopback).
                return poll_ready_none()
            except BlockingIOError:
                _reactor_register_fd(self._sock.fileno(), EPOLLOUT, waker)
                return poll_pending()
        # Woken on writability: the connect attempt has completed. SO_ERROR
        # carries 0 on success or the failure errno (e.g. ECONNREFUSED).
        err = self._sock.getsockopt_int(SOL_SOCKET, SO_ERROR)
        if err == 0:
            return poll_ready_none()
        # Same errno-keyed taxonomy as socket's own raise helpers: a refused
        # connect surfaces as ConnectionRefusedError (CPython asyncio parity).
        # The message is CPython asyncio's own wording (its _sock_connect_cb
        # raises OSError(err, f'Connect call failed {address}'), not the OS
        # strerror text).
        msg = f"Connect call failed {self._addr}"
        _maybe_raise_connection_error(err, msg)
        raise SocketError(err, msg)


class SleepFuture:
    """Awaits a steady-clock deadline; conforms to the Awaitable[None]
    shape (poll + `_cancel_pending` field that the runtime's
    `Task::cancel` flips).

    Like CPython's `asyncio.sleep`, the delay starts when the sleep is
    first polled (not when `sleep()` is called) and the first poll always
    suspends: a positive delay registers a timer, a non-positive one
    requeues the task once (CPython's bare `yield` in `__sleep0`). Timers
    therefore wake sleepers in the order their delays started, even when
    a task was preempted before its first poll.
    """

    seconds: float
    deadline: float
    registered: bool
    _cancel_pending: bool

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.deadline = 0.0
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
        if not self.registered:
            self.registered = True
            if self.seconds <= 0.0:
                waker.wake()
            else:
                self.deadline = monotonic() + self.seconds
                _register_timer_at(self.deadline, waker)
            return poll_pending()
        if self.seconds <= 0.0 or monotonic() >= self.deadline:
            return poll_ready_none()
        return poll_pending()


# Park the calling coroutine for `seconds` seconds. Returns an
# Own[Task[None]] whose underlying SleepFuture starts the delay and
# suspends on its first poll; the run loop wakes it when the deadline
# arrives (or on the next pass, for a non-positive delay).
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

    Starts the deadline and, for a positive timeout, registers a one-shot
    timer with the current executor on first poll; a positive timeout
    never expires on that
    poll, so the inner always runs to its first suspension (CPython arms
    its timeout with `call_at`). When the deadline elapses, propagates
    `cancel()` to the
    inner frame and keeps polling it until it returns -- translating
    the resulting `CancelledError` into `TimeoutError`. An external
    cancel (outer task cancellation) propagates to the inner unchanged
    and re-raises `CancelledError`. The deadline timer can fire after
    the inner already completed; the generation guard in
    `Executor.mark_runnable` swallows the late wake.
    """

    _inner: Box[Cancellable[T]]
    _timeout: float
    _deadline: float
    _registered: bool
    _cleanup: bool
    _timed_out: bool
    _cancel_pending: bool

    def __init__(self, coro: Own[Cancellable[T]], timeout: float) -> None:
        self._inner = Box[Cancellable[T]](coro)
        self._timeout = timeout
        self._deadline = 0.0
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

        expired = self._timeout <= 0.0
        if not self._registered:
            self._registered = True
            self._deadline = monotonic() + self._timeout
            if not expired:
                _register_timer_at(self._deadline, waker)
        elif not expired:
            expired = monotonic() >= self._deadline

        # Deadline elapsed and we have not started cleanup yet:
        # propagate cancel to the inner and pump it until it returns.
        # A non-positive timeout takes this branch on the first poll.
        if not self._cleanup and expired:
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
# positive `timeout` triggers the deadline on the first poll; the inner
# still runs up to its first `await`, where CPython never starts it
# (BUGS.md#coroutine-cancelled-before-first-poll-runs-body).
#
# Outer cancellation of a task awaiting `wait_for` propagates through
# to the inner coroutine: the resume-case cancel-check (in every
# async-def coro frame) calls `cancel()` on the in-flight sub-coro
# before polling, so the inner observes `CancelledError` at its
# suspension point and can run `finally`-with-await cleanup before
# the cancellation surfaces to the caller.
# `-> Own[T]` for the same reason as `run`: the awaited inner result is
# an owned value moved up the poll chain; a bare `-> T` would make this
# a borrow contract at object-typed instantiations.
async def wait_for[T](coro: Own[Cancellable[T]], timeout: float) -> Own[T]:
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
    _completion_indices: list[int32]
    _completion_boxes: list[Box[T]]
    _settled: list[bool]
    _exc: Box[Throwable] | None
    _completed: int32
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
        i: int32 = 0
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
                except (SystemExit, KeyboardInterrupt):
                    # These end the program, not the sub-task: never a
                    # gather outcome (CPython raises them out of the loop).
                    raise
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
        orig_i: int32 = 0
        while orig_i < n:
            k: int32 = 0
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
        i: int32 = 0
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
async def gather_list[T](tasks: list[Task[T]]) -> Own[list[T]]:
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
    _result_indices: list[int32]
    _result_boxes: list[Box[T]]
    _exc_indices: list[int32]
    _exc_boxes: list[Box[Throwable]]
    _completed: int32
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
        i: int32 = 0
        if was_canceling:
            while i < n:
                if not self._settled[i]:
                    self._tasks[i].cancel()
                i += 1

        i = 0
        while i < n:
            if not self._settled[i]:
                try:
                    p = self._tasks[i].__poll__(waker)
                    if p.is_ready():
                        self._settled[i] = True
                        self._completed += 1
                        self._result_indices.append(i)
                        self._result_boxes.append(Box(p.value()))
                except (SystemExit, KeyboardInterrupt):
                    # Not a settled outcome: these end the program.
                    raise
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
        orig_i: int32 = 0
        while orig_i < n:
            settled_via_value = False
            k: int32 = 0
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
        tasks: list[Task[T]]) -> Own[list[Settled[T]]]:
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
    _exception: Box[Throwable] | None
    _waiter: Waker
    _result: UninitStorage[T]

    def __init__(self) -> None:
        self._done = False
        self._has_waiter = False
        self._exception = None
        self._waiter = Waker()
        self._result = UninitStorage[T]()

    def __del__(self) -> None:
        if self._result.has():
            self._result.reset()

    def done(self) -> bool:
        return self._done

    def set_result(self, value: Own[T]) -> None:
        # Takes ownership of `value` so nocopy types work without a
        # `__copy__` opt-in. Callers passing a named local must use
        # `tpy.copy(x)` explicitly to keep their reference alive.
        if self._done:
            raise InvalidStateError("Future already done")
        self._result.construct(value)
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
            if not self._result.has():
                raise ValueError(
                    "Future done with no result and no exception")
            # take() returns Own[T] -- pass directly as rvalue so
            # nocopy types (T with __del__ but no __copy__) flow through
            # without requiring a copy ctor.
            return poll_ready(self._result.take())
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


# Wake every parked waiter and clear the queue -- for a broadcast condition
# where, once it holds, every parked waiter should proceed.
def _wake_all(waiters: list[Waker]) -> None:
    for w in waiters:
        w.wake()
    waiters.clear()


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

    _value: int32
    _waiters: list[Waker]
    # Upper bound for release(); -1 means unbounded (plain Semaphore). The
    # bound lives here, gated in release(), rather than in a BoundedSemaphore
    # override -- TPy uses static method dispatch, so an override would only
    # fire through a BoundedSemaphore-typed reference (and warns about it).
    _bound: int32

    def __init__(self, value: int32 = 1) -> None:
        if value < 0:
            raise ValueError("Semaphore initial value must be >= 0")
        self._value = value
        self._waiters = []
        self._bound = -1

    def locked(self) -> bool:
        # CPython also reports locked while acquirers are parked, not only
        # when the count hits 0. `_waiters` may retain a since-cancelled
        # waker (the cancel-while-parked gap), so this can over-report.
        return self._value == 0 or len(self._waiters) > 0

    async def acquire(self) -> bool:
        await _SemAcquire(self)
        return True

    def release(self) -> None:
        if self._bound >= 0 and self._value >= self._bound:
            raise ValueError("BoundedSemaphore released too many times")
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


class BoundedSemaphore(Semaphore):
    """A `Semaphore` whose `release` raises `ValueError` if it would raise
    the counter above its initial value -- catches a release/acquire
    imbalance. Matches CPython. (The bound check is the gated path in
    `Semaphore.release`; see the `_bound` field there.)
    """

    def __init__(self, value: int32 = 1) -> None:
        super().__init__(value)
        self._bound = value


class QueueEmpty(Exception):
    pass


class QueueFull(Exception):
    pass


@nocopy
class Queue[T]:
    """FIFO async queue. `put`/`get` block (park, FIFO) when the queue is
    full / empty; `put_nowait`/`get_nowait` raise `QueueFull`/`QueueEmpty`
    instead. `maxsize <= 0` is unbounded. `join` blocks until every item
    delivered by `put` has been marked done via `task_done`. Matches
    CPython; single-threaded, same fairness note as `Lock`.

    Built on the `Waker`-parking mechanism: getters park while empty,
    putters while full, joiners while work is outstanding. Not awaitable
    directly (acquisition is via `get` / `put`).
    """

    _items: list[T]
    # Public, like CPython's Queue.maxsize; <= 0 means unbounded.
    maxsize: int32
    _getters: list[Waker]
    _putters: list[Waker]
    _joiners: list[Waker]
    _unfinished: int32

    def __init__(self, maxsize: int32 = 0) -> None:
        self._items = []
        self.maxsize = maxsize
        self._getters = []
        self._putters = []
        self._joiners = []
        self._unfinished = 0

    def qsize(self) -> int32:
        return len(self._items)

    def empty(self) -> bool:
        return len(self._items) == 0

    def full(self) -> bool:
        return self.maxsize > 0 and len(self._items) >= self.maxsize

    def put_nowait(self, item: Own[T]) -> None:
        if self.full():
            raise QueueFull("Queue full")
        self._items.append(item)
        self._unfinished += 1
        _wake_one(self._getters)

    def get_nowait(self) -> Own[T]:
        if self.empty():
            raise QueueEmpty("Queue empty")
        # A slot is about to free; wake a parked putter before the pop (it
        # re-polls later, by which point this synchronous pop has run).
        _wake_one(self._putters)
        return self._items.pop(0)

    async def put(self, item: Own[T]) -> None:
        await _QueueWait[T](self, 1)
        self.put_nowait(item)

    async def get(self) -> Own[T]:
        await _QueueWait[T](self, 0)
        return self.get_nowait()

    def task_done(self) -> None:
        if self._unfinished <= 0:
            raise ValueError("task_done() called too many times")
        self._unfinished -= 1
        if self._unfinished == 0:
            _wake_all(self._joiners)

    async def join(self) -> None:
        await _QueueWait[T](self, 2)

    # Called by `_QueueWait.__poll__` through a `Ptr[Queue[T]]`. Returns
    # True if the awaited condition holds now; else parks `waker` and
    # returns False. kind: 0 = get (non-empty), 1 = put (not full),
    # 2 = join (no unfinished tasks).
    def _wait_ready(self, kind: int32, waker: Waker) -> bool:
        if kind == 0:
            if len(self._items) > 0:
                return True
            self._getters.append(waker)
            return False
        if kind == 1:
            if not self.full():
                return True
            self._putters.append(waker)
            return False
        if self._unfinished == 0:
            return True
        self._joiners.append(waker)
        return False


@nocopy
class _QueueWait[T]:
    """Private awaitable backing `Queue.get` / `put` / `join` (see
    `_LockAcquire`). Holds a `Ptr[Queue[T]]`; the `kind` selects the park
    condition checked by `Queue._wait_ready`.
    """

    _q: Ptr[Queue[T]]
    _kind: int32

    def __init__(self, q: Ptr[Queue[T]], kind: int32) -> None:
        self._q = q
        self._kind = kind

    def cancel(self) -> None:
        pass

    def __poll__(self, waker: Waker) -> Own[Poll[None]]:
        if self._q._wait_ready(self._kind, waker):
            return poll_ready_none()
        return poll_pending()


class EventLoop:
    """Handle to the running event loop, returned by `get_running_loop()`.

    Exposes CPython's low-level socket coroutine methods on a non-blocking
    socket. `sock_recv` / `sock_sendall` / `sock_accept` / `sock_connect`
    are sync factories returning the socket awaitables above (the
    `gather(...) -> Own[...]` shape); the user awaits the result. Stateless
    -- the reactor is reached via the current-executor global; the handle
    exists to match CPython's `loop.sock_*` surface. The high-level streams
    layer (`open_connection` -> `StreamReader`/`StreamWriter`) is built on
    these; `start_server` is a deferred follow-up (see TODO.md).
    """

    def __init__(self) -> None:
        pass

    def sock_recv(self, sock: socket, n: int32) -> Own[_SockRecv]:
        return _SockRecv(sock, n)

    def sock_sendall(self, sock: socket, data: bytes) -> Own[_SockSendAll]:
        return _SockSendAll(sock, data)

    def sock_accept(self, sock: socket
                    ) -> Own[_SockAccept]:
        return _SockAccept(sock)

    def sock_connect(self, sock: socket,
                     address: tuple[str, int32]) -> Own[_SockConnect]:
        return _SockConnect(sock, address)


# Return a handle to the running event loop. Raises RuntimeError outside
# `asyncio.run` (matches CPython's get_running_loop).
def get_running_loop() -> Own[EventLoop]:
    if _get_current_executor() is None:
        raise RuntimeError("no running event loop")
    return EventLoop()


class IncompleteReadError(EOFError):
    """Raised by `StreamReader.readexactly` / `readuntil` when EOF arrives
    before the read completes. Mirrors `asyncio.IncompleteReadError`: `partial`
    is what was read, `expected` the requested count (None for `readuntil`,
    where the total length is not known up front)."""

    partial: bytes
    expected: int32 | None

    def __init__(self, partial: bytes, expected: int32 | None) -> None:
        super().__init__("incomplete read")
        self.partial = partial
        self.expected = expected


@nocopy
class StreamReader:
    """Buffered read side of a stream, over the reactor's `sock_recv`.

    Holds a shared `Rc[socket]` (the writer holds a clone -- the socket
    outlives both halves and stays alive across awaits via the Rc, not via
    `close`) and an owned `bytes` buffer it fills with `await loop.sock_recv`
    on demand. `read` / `readexactly` / `readline` mirror
    `asyncio.StreamReader`."""

    _sock: Rc[socket]
    _buf: bytes
    _eof: bool

    def __init__(self, sock: Own[Rc[socket]]) -> None:
        self._sock = sock
        self._buf = bytes()
        self._eof = False

    def at_eof(self) -> bool:
        return self._eof and len(self._buf) == 0

    # Returns the chunk length so callers can distinguish EOF (0) from data.
    async def _fill(self) -> int32:
        loop = get_running_loop()
        chunk = await loop.sock_recv(self._sock.get(), 65536)
        if len(chunk) == 0:
            self._eof = True
        else:
            self._buf = self._buf + chunk
        return len(chunk)

    def _take(self, n: int32) -> bytes:
        # Materialize owned head before reassigning `_buf` (a no-step slice
        # is a borrow into the old buffer).
        head = bytes(self._buf[:n])
        self._buf = bytes(self._buf[n:])
        return head

    async def read(self, n: int32) -> bytes:
        """Read up to `n` bytes, returning as soon as any data is buffered
        (fewer than `n` is normal); empty at EOF. `n < 0` reads until EOF.
        Returns early like CPython -- does NOT wait for the full `n`."""
        if n < 0:
            while not self._eof:
                await self._fill()
            return self._take(len(self._buf))
        if len(self._buf) == 0 and not self._eof:
            await self._fill()
        take = n if n < len(self._buf) else len(self._buf)
        return self._take(take)

    async def readexactly(self, n: int32) -> bytes:
        """Read exactly `n` bytes; raise `IncompleteReadError` if EOF comes
        first (carrying the partial bytes read)."""
        if n < 0:
            raise ValueError("readexactly size can not be less than zero")
        while len(self._buf) < n and not self._eof:
            await self._fill()
        if len(self._buf) < n:
            raise IncompleteReadError(self._take(len(self._buf)), n)
        return self._take(n)

    async def readline(self) -> bytes:
        """Read until (and including) the next `\\n`, or until EOF. Returns
        the partial line at EOF without raising (matches CPython)."""
        idx = self._buf.find(b"\n")
        while idx < 0 and not self._eof:
            await self._fill()
            idx = self._buf.find(b"\n")
        if idx < 0:
            return self._take(len(self._buf))
        return self._take(idx + 1)

    async def readuntil(self, separator: bytes) -> bytes:
        """Read until (and including) `separator`. Raise `IncompleteReadError`
        (carrying the partial data) if EOF arrives before the separator is
        found. No buffer limit -- like `readline` / `read`, this v1 streams
        layer does not enforce CPython's `limit` / `LimitOverrunError`."""
        if len(separator) == 0:
            raise ValueError("Separator should be at least one-byte string")
        idx = self._buf.find(separator)
        while idx < 0 and not self._eof:
            await self._fill()
            idx = self._buf.find(separator)
        if idx < 0:
            raise IncompleteReadError(self._take(len(self._buf)), None)
        return self._take(idx + len(separator))


@nocopy
class StreamWriter:
    """Buffered write side of a stream, over the reactor's `sock_sendall`.

    Holds a clone of the same `Rc[socket]` as its `StreamReader`. `write`
    only appends to an owned buffer (synchronous, can't send); `drain`
    actually sends it via `await loop.sock_sendall`. Unlike CPython -- whose
    `close` flushes the transport buffer asynchronously -- `close` here is
    synchronous and does NOT flush, so you must `await drain()` before
    `close()`; closing with unflushed bytes raises rather than dropping
    them silently."""

    _sock: Rc[socket]
    _buf: bytes
    _closed: bool

    def __init__(self, sock: Own[Rc[socket]]) -> None:
        self._sock = sock
        self._buf = bytes()
        self._closed = False

    def write(self, data: bytes) -> None:
        self._buf = self._buf + data

    async def drain(self) -> None:
        if len(self._buf) > 0:
            loop = get_running_loop()
            await loop.sock_sendall(self._sock.get(), self._buf)
            self._buf = bytes()

    def close(self) -> None:
        # Fail loud on unflushed data rather than silently dropping it (close
        # can't send -- see the class docstring); the caller must drain first.
        if len(self._buf) > 0:
            raise RuntimeError(
                "StreamWriter.close() with unflushed data; await drain() first")
        if not self._closed:
            self._closed = True
            self._sock.get().close()

    def is_closing(self) -> bool:
        return self._closed

    async def wait_closed(self) -> None:
        # close() already shut the socket down synchronously; nothing to
        # await. Present for CPython-shape `close(); await wait_closed()`.
        pass


# Mirrors `asyncio.open_connection` (the host:port client form). The
# connection's socket is shared by the reader and writer via an `Rc[socket]`
# cell so either can drive it and it outlives both across awaits.
async def open_connection(
        host: str, port: int32) -> tuple[Own[StreamReader], Own[StreamWriter]]:
    loop = get_running_loop()
    sock = socket(AF_INET, SOCK_STREAM)
    sock.setblocking(False)
    await loop.sock_connect(sock, (host, port))
    cell = Rc.new(sock)
    # Build the tuple from fresh rvalues, not named locals: an async return
    # of a tuple of named @nocopy locals copies the elements instead of
    # moving them, which fails to compile.
    return (StreamReader(cell.clone()), StreamWriter(cell.clone()))


# Background accept loop spawned by `start_server`. Owns its own `Rc[socket]`
# clone so it outlives the `Server` handle across awaits. The loop is stopped
# by cancellation (`Server.close` cancels this task) rather than by catching a
# listener-closed error: a `try`-scoped binding of `conn` is not yet recognized
# as movable, so the bare `await` keeps `conn`'s last-use move into `Rc.new`.
async def _accept_loop(
        listener: Own[Rc[socket]],
        cb: Callable[[Own[StreamReader], Own[StreamWriter]],
                     Own[Cancellable[None]]]) -> None:
    loop = get_running_loop()
    while True:
        conn, addr = await loop.sock_accept(listener.get())
        cell = Rc.new(conn)
        create_task(cb(StreamReader(cell.clone()), StreamWriter(cell.clone())))


@nocopy
class _ServerSockets:
    """The object behind `Server.sockets`, mirroring CPython's
    `server.sockets[0].getsockname()` surface. Holds its own `Rc[socket]` clone
    and hands out a (readonly) borrow of the listener per index -- enough for
    the common `getsockname()` read; mutating socket methods are not exposed
    (the borrow is readonly). The single listener is returned for every index."""

    _sock: Rc[socket]

    def __init__(self, sock: Own[Rc[socket]]) -> None:
        self._sock = sock

    @auto_readonly
    def __getitem__(self, i: int32) -> auto_readonly[socket]:
        return self._sock.get()


@nocopy
class Server:
    """Handle returned by `start_server`, sharing the listening socket with the
    background accept task via `Rc[socket]`. Accepts in the background from
    creation (CPython parity -- it serves before `serve_forever`). The bound
    address is read via `server.sockets[0].getsockname()`, as in CPython.

    `close()` stops accepting and leaves in-flight handler tasks running,
    `serve_forever()` serves until cancelled then re-raises `CancelledError`,
    and `async with server as s` binds an alias of the server (all CPython
    parity). v1 divergence: `wait_closed()` is a no-op rather than awaiting
    in-flight connections to drain."""

    _listener: Rc[socket]
    _task: Task[None]
    sockets: _ServerSockets

    def __init__(self, listener: Own[Rc[socket]],
                 task: Own[Task[None]]) -> None:
        # Build the sockets view first (cloning the Rc is allowed here, in a
        # non-const ctor) before moving the listener into the field.
        self.sockets = _ServerSockets(listener.clone())
        self._listener = listener
        self._task = task

    def close(self) -> None:
        self._listener.get().close()
        self._task.cancel()

    async def serve_forever(self) -> None:
        # Serves until cancelled, like CPython: cancelling this coroutine (or a
        # close() elsewhere, which cancels the accept task we await) surfaces as
        # CancelledError, which closes the server (idempotent) and re-raises.
        try:
            await self._task
        except CancelledError:
            self.close()
            raise

    async def wait_closed(self) -> None:
        # No-op (v1): returns immediately. Unlike CPython it does NOT wait for
        # in-flight handler tasks to drain -- the canonical `close(); await
        # wait_closed()` shutdown returns while connections may still be live.
        pass

    async def __aenter__(self) -> "Server":
        # Borrow return: `async with server as s` binds `s` as an alias of
        # the server (CPython parity; @nocopy-safe -- no copy is made).
        return self

    async def __aexit__(self, exc_type: None, exc_val: None,
                        exc_tb: None) -> None:
        self.close()


# Mirrors `asyncio.start_server` (the host:port form). `cb` must be a coroutine
# factory (an `async def handler(reader, writer)`), wrapped in a task per accept
# -- mirroring CPython auto-wrapping a coroutine callback. CPython also accepts a
# plain sync callback returning None; that overload is unbuilt here (a sync cb is
# statically rejected by the type). The handler must take `Own[...]` (it owns its
# streams across awaits). IPv4 only (AF_INET).
async def start_server(
        cb: Callable[[Own[StreamReader], Own[StreamWriter]],
                     Own[Cancellable[None]]],
        host: str, port: int32) -> Own[Server]:
    listener = socket(AF_INET, SOCK_STREAM)
    listener.setsockopt_int(SOL_SOCKET, SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(128)
    listener.setblocking(False)
    cell = Rc.new(listener)
    task = create_task(_accept_loop(cell.clone(), cb))
    return Server(cell.clone(), task)
