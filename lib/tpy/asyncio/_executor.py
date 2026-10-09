# tpy: cpp_namespace("tpystd::asyncio::_executor")
# tpy: include("<tpy/async.hpp>")
# tpy: include("<tpy/stdlib/time.hpp>")
"""Internal asyncio scaffolding -- the TPy executor and the small
helpers it needs. Not part of the public asyncio API; imported only
by `lib/tpy/asyncio/` modules. See `docs/ASYNC_DESIGN.md` for the
dispatch model and `docs/ASYNC_PROGRESS.md` for port history.

`Waker` (defined in `tpy.coro`) holds a `Ptr[Awaker]` to the running
executor; `Waker.wake()` dispatches `mark_runnable` through the
`@dynamic Awaker` vtable. `Executor` inherits `Awaker` so it provides
the vtable slot directly -- no C++ ExecutorOps table, no templated
thunks, no thread-local handle.
"""
import heapq

from typing import Final, Protocol
from builtins import BaseException
from time import monotonic, sleep_until_steady
from tpy import (
    int32, uint32, Own, Ptr, Array, CancelledError, Throwable, dynamic,
    nocopy, readonly,
)
from tpy.extern import builtin_type, cpp_template
from tpy.coro import Awaker, Cancellable, Poll, Waker, poll_ready, poll_pending
from tpy.mem import UninitStorage
from tpy.unsafe import unsafe_load, unsafe_ptr
from tplib import Box
from tplib.rc import Rc
from _bindings import posix_epoll, posix_socket, posix_signal


# Type-erased task machinery is co-located with the executor (rather
# than split into a sibling submodule) to avoid the parent-package
# auto-include cycle in `tpyc/codegen_cpp/generator.py:1530+`; see
# BUGS.md.


# `Cancellable[T]` (`__poll__` + `cancel`) lives in `tpy.coro` now;
# we re-export it from `asyncio` for user-facing API typing.


# T-erased view of a task for the executor's slot table. TaskState[T]
# structurally conforms via its poll_any / cancel_any methods; the
# slot table holds Box[AnyTask] without parameterization on T.
@dynamic
class AnyTask(Protocol):
    def poll_any(self, waker: Waker) -> bool: ...
    def cancel_any(self) -> None: ...


@nocopy
class TaskState[T]:
    """Shared backing for a `Task[T]`.

    Owns a `Box[Cancellable[T]]` (the type-erased coroutine frame) and
    caches result/exception once the frame completes. Single-awaiter
    for v1: a second `__poll__` after Ready panics.
    """

    frame: Box[Cancellable[T]] | None
    # Single owning slot for the cached result: moves correctly element-wise
    # (the TaskState is moved into its Rc cell at creation, while the slot is
    # still empty -- the result is cached later by poll_any).
    result: UninitStorage[T]
    exc: Box[Throwable] | None
    awaiter: Waker
    done: bool
    executor_owned: bool
    # True while the frame is being polled. A cancel requested then (by
    # code the poll runs: a Ctrl-C delivered at a check point, a
    # `Task.cancel()` of the running task) is recorded in `must_cancel`
    # rather than flagged on the frame, so it lands at the next real
    # suspension as CPython's Task._must_cancel does: an await that
    # completes without suspending still runs, and a frame that returns
    # in this poll completes as cancelled.
    polling: bool
    must_cancel: bool

    def __init__(self, frame: Own[Box[Cancellable[T]]]) -> None:
        self.frame = frame
        self.result = UninitStorage[T]()
        self.exc = None
        self.awaiter = Waker()
        self.done = False
        self.executor_owned = False
        self.polling = False
        self.must_cancel = False

    def __del__(self) -> None:
        # Redundant with the slot's own RAII drop -- left until removing this
        # __del__ is verified not to change the type's move/value-class codegen
        # (TODO). A no-op when the result was already consumed.
        if self.result.has():
            self.result.reset()

    # User-facing poll. Drives the frame for non-executor-owned tasks;
    # for executor-owned tasks, parks (the executor's poll_any drives).
    def __poll__(self, w: Waker) -> Own[Poll[T]]:
        if self.done:
            exc = self.exc
            if exc is not None:
                raise exc
            if not self.result.has():
                raise RuntimeError(
                    "Task: __poll__ after Ready was already consumed")
            return poll_ready(self.result.take())
        if self.executor_owned:
            self.awaiter = w
            return poll_pending()
        # Non-executor-owned: drive the frame directly.
        frame = self.frame
        if frame is None:
            raise RuntimeError("Task: __poll__ on empty TaskState")
        try:
            self.polling = True
            p = frame.get().__poll__(w)
            self.polling = False
            if self._take_must_cancel(p.is_ready(), w):
                return poll_pending()
            if p.is_ready():
                self.done = True
            return p
        except BaseException as e:
            self._end_poll()
            self.done = True
            self.exc = Box(e.clone())
            raise

    # Applies a cancel requested during the poll that just returned (see
    # `polling`). Pending: the cancel goes to the frame, and the task is
    # woken so the next poll delivers it at the suspension. Ready: the
    # task is cancelled instead of completing (CPython's Task.__step); the
    # value is dropped. Returns True when the task is still pending.
    def _take_must_cancel(self, ready: bool, w: Waker) -> bool:
        if not self.must_cancel:
            return False
        self.must_cancel = False
        if ready:
            raise CancelledError()
        frame = self.frame
        if frame is not None:
            frame.get().cancel()
        w.wake()
        return True

    def _end_poll(self) -> None:
        self.polling = False
        self.must_cancel = False

    # AnyTask interface: drives the frame and caches result/exc. Used
    # by the executor's slot table via the TaskStateView adapter.
    def poll_any(self, w: Waker) -> bool:
        if self.done:
            return True
        frame = self.frame
        if frame is None:
            return True
        try:
            self.polling = True
            p = frame.get().__poll__(w)
            self.polling = False
            if self._take_must_cancel(p.is_ready(), w):
                return False
            if p.is_ready():
                self.done = True
                self.result.construct(p.value())
                self.awaiter.wake()
                return True
            return False
        except (SystemExit, KeyboardInterrupt) as e:
            # Stored like any outcome, then raised out of the run, as
            # CPython's Task.__step does: these end the program, not the task.
            self._end_poll()
            self.done = True
            self.exc = Box(e.clone())
            self.awaiter.wake()
            raise
        except BaseException as e:
            self._end_poll()
            self.done = True
            self.exc = Box(e.clone())
            self.awaiter.wake()
            return True

    def cancel_any(self) -> None:
        if self.done:
            return
        if self.polling:
            self.must_cancel = True
            return
        frame = self.frame
        if frame is not None:
            frame.get().cancel()


# Adapter that exposes a TaskState[T] through the non-generic AnyTask
# protocol. Holds an Rc clone of the same TaskState; the executor's
# slot table holds Box[AnyTask] wrapping this view.
@nocopy
class TaskStateView[T]:
    state: Rc[TaskState[T]]

    def __init__(self, state: Own[Rc[TaskState[T]]]) -> None:
        self.state = state

    def poll_any(self, w: Waker) -> bool:
        return self.state.get().poll_any(w)

    def cancel_any(self) -> None:
        self.state.get().cancel_any()


@nocopy
class Task[T]:
    """Type-erased async task. Holds a `Rc[TaskState[T]]` shared with
    the executor's slot table for spawned tasks.

    Regular @nocopy generic-class machinery: the C++ name
    (`::tpystd::asyncio::_executor::Task<T>`) is derived from this
    module's `# tpy: cpp_namespace` directive through
    `NominalType._fallback_cpp_base_name`, and awaitability comes from
    the `__poll__(self, w: Waker) -> Poll[T]` method below being
    structurally matched by `_extract_awaitable_inner` in
    `tpyc/sema/expressions.py`.
    """

    _state: Rc[TaskState[T]]
    # Default-constructed (null awaker) for non-executor-owned tasks
    # (`task_from_coro`); reassigned by `create_task` to a Waker stamped
    # with the spawn slot's (slot_id, generation). `cancel()` calls
    # `wake()` on it so the slot is marked runnable promptly -- the
    # in-flight frame observes the cancel flag on its next poll instead
    # of waiting on a timer / IO wake. Wake on a null-awaker Waker is a
    # safe no-op (see Waker.wake in tpy.coro), so the non-executor-
    # owned case stays unchanged.
    #
    # Lifetime: this Waker holds a raw `Ptr[Awaker]` into the running
    # `Executor`. A Task[T] handle that survives `asyncio.run`'s scope
    # and is later cancel()'d will dispatch through a dangling pointer
    # -- same invariant `_ExecutorScope` (below) documents for every
    # other stamped Waker. v1 asyncio.run drains spawned tasks during
    # teardown to make this case unreachable in practice.
    _waker: Waker

    def __init__(self, state: Own[Rc[TaskState[T]]]) -> None:
        self._state = state
        self._waker = Waker()

    def __poll__(self, w: Waker) -> Own[Poll[T]]:
        return self._state.get().__poll__(w)

    def cancel(self) -> None:
        self._state.get().cancel_any()
        # Wake routes through the @dynamic Awaker vtable to the
        # executor's mark_runnable. The generation guard there filters
        # late wakes against a completed slot, so this is safe even
        # after the underlying task has already finished.
        self._waker.wake()

    # Cheap duplication: Task is an Rc handle into the shared TaskState,
    # so clone() just bumps the refcount. Used by asyncio.gather and
    # asyncio.gather_list to take owned task handles without consuming
    # the caller's list/varargs.
    #
    # WARNING (v1.5): the clone aliases the SAME TaskState as `self`.
    # TaskState is single-awaiter (one `awaiter: Waker` slot, one cached
    # result slot consumed on first __poll__ that observes Ready). Two
    # live handles must not be awaited concurrently and must not both
    # consume the result -- only one consumer survives, the other's
    # poll panics or its waker is overwritten. Likewise `cancel()` on
    # either handle propagates through the shared TaskState to BOTH;
    # the clone ALSO inherits the parent's stamped `_waker` (see field
    # comment above), so cancel-via-clone marks the same executor slot
    # runnable -- load-bearing for `_GatherFuture._propagate_cancel`,
    # which cancels its Rc-cloned tasks and relies on the wake landing
    # on the user's original spawn slot. `gather` and `gather_list` are
    # the only safe internal users today: each drives its clones
    # exclusively, and the user is expected to drop their original
    # `tasks[i]` (gather_list) or stop using each positional Task arg
    # (gather) once they've handed off to either entrypoint. A multi-
    # awaiter TaskState is filed in TODO.md.
    def clone(self) -> Own[Task[T]]:
        t = Task[T](self._state.clone())
        t._waker = self._waker
        return t


def task_from_coro[T](coro: Own[Cancellable[T]]) -> Own[Task[T]]:
    """Box an awaitable into a heap-allocated Task[T] without
    registering with an executor (no `asyncio.run` required).
    """
    return _build_task[T](coro, False)


def make_executor_owned_task[T](coro: Own[Cancellable[T]]) -> Own[Task[T]]:
    """Build a Task[T] flagged `executor_owned=True` (ready to be
    spawned via the executor's slot table)."""
    return _build_task[T](coro, True)


def _build_task[T](coro: Own[Cancellable[T]], executor_owned: bool) -> Own[Task[T]]:
    frame = Box[Cancellable[T]](coro)
    state = TaskState[T](frame)
    state.executor_owned = executor_owned
    return Task[T](Rc.new(state))


def task_to_any_box[T](task: Task[T]) -> Own[Box[AnyTask]]:
    """Mirror a `Task[T]`'s shared state into a `Box[AnyTask]` for the
    executor's slot table."""
    return Box[AnyTask](TaskStateView[T](task._state.clone()))


# --- Awaker-side helpers (call sites inside Executor) -------------------


def _make_waker(handle: Awaker, task_id: int32,
                generation: int32) -> Waker:
    return Waker(handle, task_id, generation)


class TimerEntry:
    """One entry in the executor's timer min-heap. Ordered by deadline."""
    deadline: float
    waker: Waker

    def __init__(self, deadline: float, waker: Waker) -> None:
        self.deadline = deadline
        self.waker = waker

    def __lt__(self, other: 'TimerEntry') -> bool:
        return self.deadline < other.deadline


@nocopy
class Slot:
    """One entry in the executor's slot table.

    Holds a `Box[AnyTask]` driving a spawned task (None once the task
    completes). The generation counter advances when the slot
    completes, invalidating any stale wakers that were handed out
    before completion -- `Waker.wake()` (in `tpy.coro`) and the
    runnable-drain loop in `Executor.drain_runnable` both check the
    slot's generation and silently drop late wakes.

    `runnable` mirrors the slot's presence in the executor's runnable
    deque: set true when `mark_runnable` adds the slot's id to the
    queue, cleared when the executor pops it to poll. `staged` is set
    while the runnable slot waits in the executor's staged queue instead.
    """

    box: Box[AnyTask] | None
    generation: int32
    runnable: bool
    staged: bool

    def __init__(self) -> None:
        self.box = None
        self.generation = 0
        self.runnable = False
        self.staged = False

    @readonly
    def is_done(self) -> bool:
        return self.box is None


# epoll_ctl ops + the reactor's drain-batch size. Kept here (not in
# posix_epoll.py, which stays declaration-only) the way socket.py hardcodes
# the AF_* wire values. The EPOLLIN / EPOLLOUT interest masks live in
# `asyncio/__init__.py` next to the fd-awaitable that passes them. The
# batch size must equal kMaxBatch in runtime/cpp/src/stdlib/epoll_impl.cpp.
_EPOLL_CTL_ADD: Final[int32] = 1
_EPOLL_CTL_DEL: Final[int32] = 2
_EPOLL_CTL_MOD: Final[int32] = 3
_REACTOR_BATCH: Final[int32] = 64
# EPOLLIN, for the executor's own SIGINT wake fd (asyncio/__init__.py's
# EPOLLIN is out of reach here: it imports this module).
_EPOLLIN: Final[uint32] = 0x001


class Reactor(Protocol):
    """The interface an asyncio I/O reactor implements.

    A reactor turns fd readiness into `Waker` wakes: `register_fd` arms an
    fd for an interest mask and parks a waker; `poll` blocks up to
    `timeout_ms` for readiness and wakes the parked wakers of ready fds;
    `unregister_fd` disarms an fd (cancellation / cleanup). `count` reports
    how many fds are armed (the executor uses it to decide whether I/O is
    a live wake source). `EpollReactor` is the only backend today.

    (`register_fd` / `unregister_fd`, not `register` / `unregister`,
    because `register` is a reserved C++ keyword and the structural-protocol
    conformance concept would emit an unparseable `t.register(...)`.)

    Designed against epoll; kqueue / io_uring backends would implement the
    same surface. Making the executor's reactor field protocol-typed so a
    user can swap a backend into `asyncio.run` is a deferred follow-up
    (see TODO.md); the executor holds the concrete `EpollReactor` today.
    """
    def register_fd(self, fd: int32, events: uint32, waker: Waker) -> None: ...
    def unregister_fd(self, fd: int32) -> None: ...
    def poll(self, timeout_ms: int32) -> None: ...
    def count(self) -> int32: ...
    def close(self) -> None: ...


@nocopy
class EpollReactor:
    """epoll-backed `Reactor` (Linux). Owns an epoll fd and a single-waiter
    `fd -> Waker` registry. Registration is one-shot: a fired fd is removed
    from epoll before its waker is woken, so the fd-awaitable re-registers
    on its next would-block. RAII-closes the epoll fd in `__del__`.

    Single waiter per fd (read OR write at a time) is sufficient for the
    current socket surface, where a coroutine awaits one direction at a
    time. Independent read+write waiters on one fd is a deferred follow-up.
    """

    _epfd: int32
    _waiters: dict[int32, Waker]
    # Raw scratch buffers for epoll_wait output (written via unsafe_ptr, read
    # back by index). Trivial fixed buffers -> Array (memcpy-movable), not the
    # @nomove UninitArrayStorage, so EpollReactor stays movable member-wise.
    # TODO: Array value-initializes (zeros) these; for a write-before-read
    # scratch buffer that's wasted. Switch to an uninitialized-yet-trivially-
    # movable storage once the TriviallyRelocatable bound lands (see TODO.md).
    # Negligible today (EpollReactor is constructed lazily, once per run).
    _out_fds: Array[int32, 64]
    _out_events: Array[uint32, 64]

    def __init__(self) -> None:
        epfd = posix_epoll.epoll_create()
        if epfd < 0:
            raise RuntimeError("asyncio reactor: epoll_create failed")
        self._epfd = epfd
        self._waiters = {}
        self._out_fds = Array[int32, 64]()
        self._out_events = Array[uint32, 64]()

    def __del__(self) -> None:
        self.close()

    def register_fd(self, fd: int32, events: uint32, waker: Waker) -> None:
        # Re-arm with MOD if the fd is still tracked (a prior would-block
        # that has not fired yet); ADD otherwise. `_waiters` membership
        # mirrors epoll membership because `poll` removes both together.
        if fd in self._waiters:
            posix_epoll.epoll_ctl(self._epfd, _EPOLL_CTL_MOD, fd, events)
        else:
            posix_epoll.epoll_ctl(self._epfd, _EPOLL_CTL_ADD, fd, events)
        self._waiters[fd] = waker

    def unregister_fd(self, fd: int32) -> None:
        if fd in self._waiters:
            posix_epoll.epoll_ctl(self._epfd, _EPOLL_CTL_DEL, fd, 0)
            del self._waiters[fd]

    def count(self) -> int32:
        return len(self._waiters)

    @readonly
    def has_fd(self, fd: int32) -> bool:
        return fd in self._waiters

    def poll(self, timeout_ms: int32) -> None:
        if len(self._waiters) == 0:
            return
        n = posix_epoll.epoll_wait(self._epfd, unsafe_ptr(self._out_fds),
                                   unsafe_ptr(self._out_events),
                                   _REACTOR_BATCH, timeout_ms)
        i: int32 = 0
        while i < n:
            fd = unsafe_load(unsafe_ptr(self._out_fds), uint32(i))
            # Disarm before waking (one-shot): the awaitable re-registers
            # on its next would-block.
            posix_epoll.epoll_ctl(self._epfd, _EPOLL_CTL_DEL, fd, 0)
            if fd in self._waiters:
                w = self._waiters[fd]
                del self._waiters[fd]
                w.wake()
            i += 1

    def close(self) -> None:
        if self._epfd >= 0:
            posix_socket.close(self._epfd)
            self._epfd = -1


@nocopy
class Executor(Awaker):
    """v1 asyncio executor: runnable-queue + timer-heap driver.

    Spawned tasks live in indexed slots; `Waker.wake()` marks a slot
    runnable if the slot is still live and the generation matches.
    Timers store the parked task's waker so a fired timer wakes only
    the task that registered it.

    Inherits `Awaker` so a `Ptr[Awaker]` stamped on each handed-out
    Waker dispatches `mark_runnable` / `register_timer` back to this
    instance through the @dynamic vtable. See `docs/ASYNC_PROGRESS.md`
    for the v1.2 step 7 pivot history.
    """

    slots: list[Slot]
    runnable_q: list[int32]
    # Tasks woken by a wait's wake sources (fd readiness, due timers). They
    # join runnable_q after the batch that follows the wait: CPython runs
    # the ready fd / timer callback in that batch, and it only completes
    # the future the task awaits, whose wakeup the task gets in the batch
    # after (a call_soon). A task waking one during that batch moves it
    # into runnable_q at that point (see mark_runnable).
    staged_q: list[int32]
    # True while the wait's wake sources run, so mark_runnable stages.
    staging: bool
    timer_heap: list[TimerEntry]
    # Lazily created on the first fd registration: a pure-timer / pure-CPU
    # program never opens an epoll fd. The second wake source alongside the
    # timer heap.
    reactor: EpollReactor | None
    # True while the run takes part in signal delivery: the loop runs the
    # handlers of the signals that arrive while it waits; gates the signal
    # poll in run_until.
    signals_armed: bool
    # The signal layer's wake fd while armed (else -1).
    signal_fd: int32
    # Ctrl-Cs asyncio.run's SIGINT handler saw during this run (on_sigint).
    interrupt_count: int32
    # The root task's slot, once run_until starts (else -1).
    root_id: int32

    def __init__(self) -> None:
        # Backstop for `asyncio.run`'s nested-loop check: a non-null
        # current_executor means a `_ExecutorScope` is already active.
        # Bare `Executor()` in unit tests is unaffected because those
        # tests never set the global.
        if _get_current_executor() is not None:
            raise RuntimeError(
                "Executor: another executor is already running "
                "(nested asyncio.run or leaked _ExecutorScope)")
        self.slots = []
        self.runnable_q = []
        self.staged_q = []
        self.staging = False
        self.timer_heap = []
        self.reactor = None
        self.signals_armed = False
        self.signal_fd = -1
        self.interrupt_count = 0
        self.root_id = -1

    def register_timer(self, deadline_seconds: float, waker: Waker) -> None:
        heapq.heappush(self.timer_heap, TimerEntry(deadline_seconds, waker))

    # Lazily opens the reactor on the first fd registration so a pure-timer
    # / pure-CPU program never allocates an epoll fd.
    def register_fd(self, fd: int32, events: uint32, waker: Waker) -> None:
        if self.reactor is None:
            self.reactor = EpollReactor()
        reactor = self.reactor
        if reactor is not None:
            reactor.register_fd(fd, events, waker)

    def unregister_fd(self, fd: int32) -> None:
        reactor = self.reactor
        if reactor is not None:
            reactor.unregister_fd(fd)

    # Milliseconds until the nearest timer fires (the epoll_wait timeout):
    # -1 (block forever) when no timer is pending, 0 when one is already
    # due, else the rounded-up delta. Capped to keep the int32 from
    # overflowing on far-future deadlines.
    def _next_timer_timeout_ms(self) -> int32:
        if len(self.timer_heap) == 0:
            return -1
        delta = self.timer_heap[0].deadline - monotonic()
        if delta <= 0.0:
            return 0
        ms = delta * 1000.0
        if ms >= 2000000000.0:
            return 2000000000
        return int32(ms) + 1

    # Mint a Waker stamped with the given slot identity. Used by
    # `asyncio.create_task` to stash a wake-handle on the Task so its
    # `cancel()` can mark the slot runnable promptly. Lives on Executor
    # rather than as a free function so the call site can pass a
    # method receiver instead of trying to coerce `Ptr[Executor]` to
    # the `Awaker` protocol param of `_make_waker`.
    def make_waker_for_slot(self, slot_id: int32, generation: int32) -> Waker:
        return _make_waker(self, slot_id, generation)

    def spawn(self, box: Own[Box[AnyTask]]) -> int32:
        new_id = len(self.slots)
        slot = Slot()
        slot.box = box
        slot.runnable = True
        self.slots.append(slot)
        self.runnable_q.append(new_id)
        return new_id

    def mark_runnable(self, slot_id: int32, generation: int32) -> None:
        if slot_id >= len(self.slots):
            return
        slot = self.slots[slot_id]
        if slot.is_done() or slot.generation != generation:
            return
        if slot.runnable:
            # A task's wake (cancel, Event.set, Queue.put, ...) of a slot a
            # wait staged queues it right here: CPython schedules the wakeup
            # where the future completes, and the staged timer / I/O
            # callback then finds the future already done.
            if slot.staged and not self.staging:
                slot.staged = False
                self.staged_q.remove(slot_id)
                self.runnable_q.append(slot_id)
            return
        slot.runnable = True
        if self.staging:
            slot.staged = True
            self.staged_q.append(slot_id)
        else:
            self.runnable_q.append(slot_id)

    def _unstage(self) -> None:
        for slot_id in self.staged_q:
            self.slots[slot_id].staged = False
        self.runnable_q.extend(self.staged_q)
        self.staged_q.clear()

    def poll_slot(self, slot_id: int32) -> bool:
        if slot_id >= len(self.slots):
            return False
        if not self.slots[slot_id].runnable or self.slots[slot_id].is_done():
            return False
        self.slots[slot_id].runnable = False
        gen = self.slots[slot_id].generation
        waker = _make_waker(self, slot_id, gen)
        # poll_any may recursively spawn new tasks which can reallocate
        # the slots vector. Hold no Slot reference across the call --
        # re-index after it returns. The AnyTask object lives on the
        # heap and is stable; only the vector storage moves.
        box = self.slots[slot_id].box
        if box is None:
            return False
        done = False
        try:
            done = box.get().poll_any(waker)
        except BaseException:
            # A task whose SystemExit / KeyboardInterrupt leaves the run is
            # finished: retire its slot so the shutdown drain does not poll
            # it again.
            self._retire(slot_id)
            raise
        if done:
            self._retire(slot_id)
        return True

    def _retire(self, slot_id: int32) -> None:
        self.slots[slot_id].box = None
        self.slots[slot_id].generation += 1

    # The shutdown drain's poll: unlike a run_until batch, it polls until
    # nothing is runnable, so a wake chain among the cancelled tasks finishes
    # within one drain attempt.
    def drain_runnable(self) -> bool:
        self._unstage()
        any_polled = False
        # TODO(async-v1.2): `list.pop(0)` is O(n); draining N runnable tasks costs
        # O(N^2). Swap `runnable_q` to `collections.deque[int32]` and use
        # `popleft()` once deque lands in TPy stdlib. See BUGS.md entry on
        # runnable_q O(n) pop.
        while len(self.runnable_q) > 0:
            slot_id = self.runnable_q.pop(0)
            if self.poll_slot(slot_id):
                any_polled = True
        return any_polled

    # Polls the tasks that are runnable as the batch starts -- CPython's one
    # pass over the ready callbacks per loop iteration. A task woken or
    # spawned during the batch runs in the next one, after I/O, timers and
    # signals had their turn, so a task re-queueing itself (`sleep(0)` in a
    # loop) cannot starve them; the tasks the previous wait woke (staged_q)
    # queue up behind those. Pops one at a time so that an exception
    # leaving the run keeps the rest queued for the shutdown drain.
    def run_batch(self) -> None:
        n = len(self.runnable_q)
        i: int32 = 0
        while i < n:
            slot_id = self.runnable_q.pop(0)
            self.poll_slot(slot_id)
            i += 1
        self._unstage()

    @readonly
    def slot_done(self, slot_id: int32) -> bool:
        if slot_id >= len(self.slots):
            return False
        return self.slots[slot_id].is_done()

    @readonly
    def has_live_tasks(self, skip_id: int32) -> bool:
        n = len(self.slots)
        i: int32 = 0
        while i < n:
            if i != skip_id and not self.slots[i].is_done():
                return True
            i += 1
        return False

    # Blocks until the nearest ready fd or due timer and collects them,
    # staging the slots they wake. Returns False when nothing is pending.
    def wait_for_event(self) -> bool:
        has_timer = len(self.timer_heap) > 0
        reactor = self.reactor
        fd_count: int32 = 0
        if reactor is not None:
            fd_count = reactor.count()
        if not has_timer and fd_count == 0:
            return False
        self.staging = True
        try:
            if reactor is not None and fd_count > 0:
                # Block in epoll_wait, bounded by the nearest timer (-1 ==
                # forever when only fds are pending). Ready fds' wakers are
                # woken inside poll(), staging their slots.
                reactor.poll(self._next_timer_timeout_ms())
            else:
                sleep_until_steady(self.timer_heap[0].deadline)
            self._fire_due_timers()
        finally:
            self.staging = False
        return True

    # The wait between two batches while tasks are still runnable: I/O is
    # polled without blocking and due timers fire, as CPython's _run_once
    # selects with timeout 0 when callbacks are ready. The tasks they wake
    # are staged (see staged_q).
    def poll_without_waiting(self) -> None:
        self.staging = True
        try:
            reactor = self.reactor
            if reactor is not None:
                reactor.poll(0)
            self._fire_due_timers()
        finally:
            self.staging = False

    def _fire_due_timers(self) -> None:
        now = monotonic()
        while len(self.timer_heap) > 0 and self.timer_heap[0].deadline <= now:
            entry = heapq.heappop(self.timer_heap)
            entry.waker.wake()

    # Cancel the root task so its CancelledError unwinds normal cleanup
    # (finally / __aexit__ / wait_closed), then mark it runnable so the next
    # drain delivers the cancel at its suspension point. Requested during the
    # root's own poll, the cancel waits in TaskState.must_cancel until that
    # poll returns.
    def _cancel_root(self, main_id: int32) -> None:
        if main_id >= len(self.slots) or self.slots[main_id].is_done():
            return
        box = self.slots[main_id].box
        if box is not None:
            box.get().cancel_any()
        self.mark_runnable(main_id, self.slots[main_id].generation)

    # asyncio.run's SIGINT handler (CPython's Runner._on_sigint): the first
    # Ctrl-C cancels the root for graceful shutdown, a later one -- or one
    # after the root finished -- raises KeyboardInterrupt where it is
    # delivered. Out of the run, the drain then cancels the root once more,
    # like CPython's asyncio.run (Runner.close cancels every unfinished
    # task). Delivered inside a task's poll -- the root's own included -- the
    # cancel takes effect at the root's next suspension, or cancels the root
    # if it returns first.
    def on_sigint(self) -> None:
        self.interrupt_count += 1
        if (self.interrupt_count == 1 and self.root_id >= 0
                and not self.slot_done(self.root_id)):
            self._cancel_root(self.root_id)
            return
        raise KeyboardInterrupt()

    # Runs, after each wait (blocking or not), the handlers of the signals no
    # coroutine's check point took (they arrived while the loop waited, or
    # while a task computed with no check point); a handler's exception
    # leaves the run. The reactor disarms an fd once it fires (one-shot), so
    # a wake fd no longer registered is one that fired: only then is it
    # drained (even with nothing pending, a byte left by a delivery that
    # raced a signal) and re-armed, or a later signal would not wake the
    # next wait. A wait that ended on I/O or a timer pays no read.
    def _deliver_after_wait(self) -> None:
        if not self.signals_armed:
            return
        reactor = self.reactor
        if reactor is None or reactor.has_fd(self.signal_fd):
            posix_signal.async_deliver(0)
            return
        posix_signal.async_deliver(1)
        reactor.register_fd(self.signal_fd, _EPOLLIN, Waker())

    # Returns True if a SIGINT interrupted the run (root cancelled for graceful
    # shutdown), False on normal completion.
    #
    # Each iteration is one CPython `_run_once` pass seen from its batch: the
    # batch of tasks runnable at its start, then the wait that collects I/O
    # and due timers (without blocking while tasks are runnable), then the
    # signal handlers. CPython stops the loop one pass after the root
    # completes (the stop is a callback the completion schedules), so the
    # tasks runnable then take one more step, after a non-blocking wait.
    def run_until(self, main_id: int32) -> bool:
        self.root_id = main_id
        stopping = False
        while True:
            self.run_batch()
            if stopping:
                return self.interrupt_count > 0
            stopping = self.slot_done(main_id)
            if stopping or len(self.runnable_q) > 0:
                self.poll_without_waiting()
            elif not self.wait_for_event():
                raise RuntimeError(
                    "asyncio.run: no progress possible (a coroutine "
                    "returned Pending with no pending timers and no "
                    "registered I/O)")
            self._deliver_after_wait()

    def drain_spawned_with_cancel(self, skip_id: int32,
                                  max_polls: int32 = 8) -> None:
        n = len(self.slots)
        i: int32 = 0
        while i < n:
            if i != skip_id and not self.slots[i].is_done():
                box = self.slots[i].box
                if box is not None:
                    box.get().cancel_any()
            i += 1
        attempt: int32 = 0
        while attempt < max_polls and self.has_live_tasks(skip_id):
            j: int32 = 0
            n2 = len(self.slots)
            while j < n2:
                if j != skip_id and not self.slots[j].is_done():
                    self.mark_runnable(j, self.slots[j].generation)
                j += 1
            # `max_polls` bounds attempts, not polls: a task that swallows
            # the cancel and loops on `sleep(0)` keeps one attempt going
            # (CPython's _cancel_all_tasks hangs on it too).
            if not self.drain_runnable():
                break
            attempt += 1


# --- Current-executor global + helpers ---------------------------------
# Defined after `Executor` so signatures can spell the concrete
# `Ptr[Executor]` without a forward-reference string. Plain (non-
# thread-local) global; v1 asyncio is single-executor per process.


_current_executor: Ptr[Executor] = None


def _get_current_executor() -> Ptr[Executor]:
    return _current_executor


def _set_current_executor(handle: Ptr[Executor]) -> None:
    global _current_executor
    _current_executor = handle


def _clear_current_executor() -> None:
    global _current_executor
    _current_executor = None


# Test-only Box[AnyTask] factory: builds a TaskState[T] without
# executor registration so tests can drive the executor directly.
def _make_any_task_for_test[T](coro: Own[Cancellable[T]]) -> Own[Box[AnyTask]]:
    task = make_executor_owned_task[T](coro)
    return task_to_any_box[T](task)


@nocopy
class _ExecutorScope:
    """RAII guard for the `_current_executor` module global. Stores
    the executor pointer on construction; clears it on `__del__`.

    Wakers stamped against this executor that fire after teardown still
    hold a (now-stale) `Ptr[Awaker]` -- that's a use-after-free at the
    raw-pointer level. v1 asyncio.run drains all spawned tasks during
    teardown to make this case unreachable in practice; multi-threaded
    async (v3+) would need a per-Waker generation guard or a refcounted
    Awaker handle.
    """

    def __init__(self, executor: Executor) -> None:
        _set_current_executor(executor)

    def __del__(self) -> None:
        _clear_current_executor()
