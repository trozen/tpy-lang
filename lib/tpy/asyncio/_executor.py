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

from typing import Protocol
from builtins import BaseException
from time import monotonic, sleep_until_steady
from tpy import Int32, Own, Ptr, Throwable, dynamic, nocopy, readonly
from tpy.extern import builtin_type, cpp_template
from tpy.coro import Awaitable, Awaker, Poll, Waker, poll_ready, poll_pending
from tpy.mem import UninitArrayStorage
from tplib import Box
from tplib.rc import Rc


# Type-erased task machinery is co-located with the executor (rather
# than split into a sibling submodule) to avoid the parent-package
# auto-include cycle in `tpyc/codegen_cpp/generator.py:1530+`; see
# BUGS.md.


# Cancellable awaitable -- the @dynamic protocol every consumer of
# asyncio's cancellation machinery accepts (`run`, `create_task`,
# `wait_for`, `Task[T]` storage, ...). Distinct from
# `tpy.coro.Awaitable[T]`: Awaitable is the structural shape for
# `await` sites + user-defined awaitables that don't promise
# cancellation; Cancellable adds the `cancel()` precondition that
# lets the task layer deliver a `CancelledError` at the awaitee's
# next suspension. gen_async.py auto-emits cancel() on every coro
# struct, so any compiled `async def` conforms automatically.
#
# Lives here (not in `tpy.coro`) because @dynamic protocols depend on
# `tplib.Adapter`, which is loaded after the implicit-stdlib chain
# that `tpy.coro` participates in. Re-exported from `asyncio` for
# user-facing API typing.
@dynamic
class Cancellable[T](Protocol):
    def __poll__(self, waker: Waker) -> Own[Poll[T]]: ...
    def cancel(self) -> None: ...


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
    result: UninitArrayStorage[T, 1]
    exc: Box[Throwable] | None
    awaiter: Waker
    done: bool
    has_result: bool
    executor_owned: bool

    def __init__(self, frame: Own[Box[Cancellable[T]]]) -> None:
        self.frame = frame
        self.result = UninitArrayStorage[T, 1]()
        self.exc = None
        self.awaiter = Waker()
        self.done = False
        self.has_result = False
        self.executor_owned = False

    def __del__(self) -> None:
        if self.has_result:
            self.result.take0()
            self.has_result = False

    # User-facing poll. Drives the frame for non-executor-owned tasks;
    # for executor-owned tasks, parks (the executor's poll_any drives).
    def __poll__(self, w: Waker) -> Own[Poll[T]]:
        if self.done:
            exc = self.exc
            if exc is not None:
                raise exc
            if not self.has_result:
                raise RuntimeError(
                    "Task: __poll__ after Ready was already consumed")
            self.has_result = False
            return poll_ready(self.result.take0())
        if self.executor_owned:
            self.awaiter = w
            return poll_pending()
        # Non-executor-owned: drive the frame directly.
        frame = self.frame
        if frame is None:
            raise RuntimeError("Task: __poll__ on empty TaskState")
        try:
            p = frame.get().__poll__(w)
            if p.is_ready():
                self.done = True
            return p
        except BaseException as e:
            self.done = True
            self.exc = Box(e.clone())
            raise

    # AnyTask interface: drives the frame and caches result/exc. Used
    # by the executor's slot table via the TaskStateView adapter.
    def poll_any(self, w: Waker) -> bool:
        if self.done:
            return True
        frame = self.frame
        if frame is None:
            return True
        try:
            p = frame.get().__poll__(w)
            if p.is_ready():
                self.done = True
                self.result.init0(p.value())
                self.has_result = True
                self.awaiter.wake()
                return True
            return False
        except BaseException as e:
            self.done = True
            self.exc = Box(e.clone())
            self.awaiter.wake()
            return True

    def cancel_any(self) -> None:
        if self.done:
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

    def __init__(self, state: Own[Rc[TaskState[T]]]) -> None:
        self._state = state

    def __poll__(self, w: Waker) -> Own[Poll[T]]:
        return self._state.get().__poll__(w)

    def cancel(self) -> None:
        self._state.get().cancel_any()


# Wrap a concrete coro in `Box[Cancellable[T]]`. `decltype({0})`
# recovers the concrete CoroT at the call site so the Adapter
# specialization picks up the right inner type. The TPy-level
# signature claims `Own[Awaitable[T]]` (the broader structural
# protocol used at every call site), but the cpp_template body
# downcasts to `Cancellable[T]` -- safe because every coro frame
# auto-emits `cancel()`, even though sema can't see that promise
# through the structural Awaitable shape. The "right" fix is a
# sema-level rule that lets async-def call results conform to
# `Cancellable[T]`; until then this template is the bypass.
@cpp_template(
    "::tpystd::tplib::box::Box<::tpystd::asyncio::_executor::Cancellable<{T}>>("
    "std::make_unique<::tpy::Adapter<"
    "::tpystd::asyncio::_executor::Cancellable<{T}>, "
    "std::remove_cvref_t<decltype({0})>>>(std::move({0})))"
)
def _box_coro[T](coro: Own[Awaitable[T]]) -> Own[Box[Cancellable[T]]]: ...


def task_from_coro[T](coro: Own[Awaitable[T]]) -> Own[Task[T]]:
    """Box an awaitable into a heap-allocated Task[T] without
    registering with an executor (no `asyncio.run` required).
    """
    return _build_task[T](coro, False)


def make_executor_owned_task[T](coro: Own[Awaitable[T]]) -> Own[Task[T]]:
    """Build a Task[T] flagged `executor_owned=True` (ready to be
    spawned via the executor's slot table)."""
    return _build_task[T](coro, True)


def _build_task[T](coro: Own[Awaitable[T]], executor_owned: bool) -> Own[Task[T]]:
    frame = _box_coro[T](coro)
    state = TaskState[T](frame)
    state.executor_owned = executor_owned
    return Task[T](Rc.new(state))


def task_to_any_box[T](task: Task[T]) -> Own[Box[AnyTask]]:
    """Mirror a `Task[T]`'s shared state into a `Box[AnyTask]` for the
    executor's slot table."""
    return Box[AnyTask](TaskStateView[T](task._state.clone()))


# --- Awaker-side helpers (call sites inside Executor) -------------------


def _make_waker(handle: Awaker, task_id: Int32,
                generation: Int32) -> Waker:
    w = Waker()
    w.awaker = handle
    w.task_id = task_id
    w.generation = generation
    return w


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
    queue, cleared when the executor pops it to poll.
    """

    box: Box[AnyTask] | None
    generation: Int32
    runnable: bool

    def __init__(self) -> None:
        self.box = None
        self.generation = 0
        self.runnable = False

    @readonly
    def is_done(self) -> bool:
        return self.box is None


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
    runnable_q: list[Int32]
    timer_heap: list[TimerEntry]

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
        self.timer_heap = []

    def register_timer(self, deadline_seconds: float, waker: Waker) -> None:
        heapq.heappush(self.timer_heap, TimerEntry(deadline_seconds, waker))

    def spawn(self, box: Own[Box[AnyTask]]) -> Int32:
        new_id = len(self.slots)
        slot = Slot()
        slot.box = box
        slot.runnable = True
        self.slots.append(slot)
        self.runnable_q.append(new_id)
        return new_id

    def mark_runnable(self, slot_id: Int32, generation: Int32) -> None:
        if slot_id >= len(self.slots):
            return
        slot = self.slots[slot_id]
        if slot.is_done() or slot.generation != generation or slot.runnable:
            return
        slot.runnable = True
        self.runnable_q.append(slot_id)

    def poll_slot(self, slot_id: Int32) -> bool:
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
        if box.get().poll_any(waker):
            self.slots[slot_id].box = None
            self.slots[slot_id].generation += 1
        return True

    def drain_runnable(self) -> bool:
        any_polled = False
        # TODO(async-v1.2): `list.pop(0)` is O(n); draining N runnable tasks costs
        # O(N^2). Swap `runnable_q` to `collections.deque[Int32]` and use
        # `popleft()` once deque lands in TPy stdlib. See BUGS.md entry on
        # runnable_q O(n) pop.
        while len(self.runnable_q) > 0:
            slot_id = self.runnable_q.pop(0)
            if self.poll_slot(slot_id):
                any_polled = True
        return any_polled

    @readonly
    def slot_done(self, slot_id: Int32) -> bool:
        if slot_id >= len(self.slots):
            return False
        return self.slots[slot_id].is_done()

    @readonly
    def has_live_tasks(self, skip_id: Int32) -> bool:
        n = len(self.slots)
        i: Int32 = 0
        while i < n:
            if i != skip_id and not self.slots[i].is_done():
                return True
            i += 1
        return False

    def wait_for_event(self) -> bool:
        if len(self.timer_heap) == 0:
            return False
        sleep_until_steady(self.timer_heap[0].deadline)
        now = monotonic()
        while len(self.timer_heap) > 0 and self.timer_heap[0].deadline <= now:
            entry = heapq.heappop(self.timer_heap)
            entry.waker.wake()
        return True

    def run_until(self, main_id: Int32) -> None:
        while True:
            if self.slot_done(main_id):
                return
            if self.drain_runnable():
                continue
            if self.slot_done(main_id):
                return
            if not self.wait_for_event():
                raise RuntimeError(
                    "asyncio.run: no progress possible (coroutine "
                    "returned Pending with no pending timers; v1 has "
                    "no I/O reactor)")

    def drain_spawned_with_cancel(self, skip_id: Int32,
                                  max_polls: Int32 = 8) -> None:
        n = len(self.slots)
        i: Int32 = 0
        while i < n:
            if i != skip_id and not self.slots[i].is_done():
                box = self.slots[i].box
                if box is not None:
                    box.get().cancel_any()
            i += 1
        attempt: Int32 = 0
        while attempt < max_polls and self.has_live_tasks(skip_id):
            j: Int32 = 0
            n2 = len(self.slots)
            while j < n2:
                if j != skip_id and not self.slots[j].is_done():
                    self.mark_runnable(j, self.slots[j].generation)
                j += 1
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
def _make_any_task_for_test[T](coro: Own[Awaitable[T]]) -> Own[Box[AnyTask]]:
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
