# tpy: cpp_namespace("tpystd::asyncio::_executor")
# tpy: include("<tpy/async.hpp>")
# tpy: include("<tpy/stdlib/time.hpp>")
"""Internal asyncio scaffolding -- the TPy executor + the small C++
runtime bridge it still needs. Not part of the public asyncio API;
imported only by `lib/tpy/asyncio/` modules. See
`docs/ASYNC_PROGRESS.md` for the port history.

TPy-side:

  * `ExecutorHandle` -- opaque `void*` value type defined in
    `tpy._core._types`. Default-constructs to null; the only @native
    method is `is_null()` (the C++ struct itself is in
    `runtime/cpp/include/tpy/async.hpp` and owns the layout).
  * `_current_executor` -- module-level `ExecutorHandle` global
    tracking the running executor. Plain (non-thread-local) global;
    v1 asyncio is single-executor per process. Multi-threaded async
    (v3+) needs TPy thread-local module globals to mirror the runtime
    side.
  * `_get_current_executor` / `_set_current_executor` /
    `_clear_current_executor` -- pure-TPy getters/setters around the
    module global.
  * `_executor_scope_teardown` -- pure-TPy function: calls the C++
    `clear_executor_ops` shim and clears the current-executor global.
  * `_make_waker` -- pure-TPy factory; constructs a `Waker` via its
    `@overload @cpp_template` aggregate-init constructor.
  * `Slot` -- the per-task entry. Wraps an `AnyTaskBox` with a
    generation counter (advanced on slot completion to invalidate
    stale wakers) and a runnable flag.
  * `Executor` -- the v1 asyncio executor; runnable-queue + timer-heap
    driver.
  * `_ExecutorScope` -- RAII guard around `_current_executor` +
    `executor_ops` (TPy side clears the global; C++ side clears the
    ops table).

C++ runtime bridge (remaining surface in `async.hpp`):

  * `Waker` POD struct + ABI-compatible TPy class stub. Layout owned
    by C++ so the templated thunks can pass Waker by value.
  * `ExecutorOps` function-pointer dispatch table + per-`ExecT`
    templated thunks (`mark_runnable_thunk`, `register_timer_thunk`).
  * `register_executor_ops_from<ExecT>` installer called from
    `Executor.__init__`.
  * `clear_executor_ops` / `executor_register_timer_seconds` --
    thin shims TPy calls into to clear/dispatch through the ops table.
"""
import heapq

from typing import Protocol
from builtins import BaseException
from time import monotonic, sleep_until_steady
from tpy import Int32, Own, ValueType, CancelledError, copy, dynamic, nocopy, readonly
from tpy.extern import builtin_type, cpp_template, native
from tpy.coro import Awaitable, Poll, Waker, ExecutorHandle, poll_ready, poll_pending
from tpy.mem import UninitArrayStorage
from tplib import Box
from tplib.rc import Rc


# Type-erased task machinery is co-located with the executor (rather
# than split into a sibling submodule) to avoid the parent-package
# auto-include cycle in `tpyc/codegen_cpp/generator.py:1530+`; see
# BUGS.md.


# Type-erased async frame for Task storage. @dynamic so concrete coro
# structs are held through Box[AsyncFrame[T]] without tracking each
# generated CoroT statically. Distinct from `tpy.coro.Awaitable[T]`:
# Awaitable is the structural shape for await-sites and user-defined
# awaitables; AsyncFrame adds the cancel() precondition required by
# the task machinery. gen_async.py auto-emits cancel() on every
# generated coro struct so structural conformance picks it up.
@dynamic
class AsyncFrame[T](Protocol):
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

    Owns a `Box[AsyncFrame[T]]` (the type-erased coroutine frame) and
    caches result/exception once the frame completes. Single-awaiter
    for v1: a second `__poll__` after Ready panics.
    """

    frame: Box[AsyncFrame[T]] | None
    result: UninitArrayStorage[T, 1]
    exc: BaseException | None
    # Storing the caught BaseException slices the dynamic type, so
    # __poll__ can't recover the concrete subclass for `except`
    # matching. Carry a dedicated flag for CancelledError (the common
    # case) and raise a fresh instance on retrieval. See BUGS.md.
    exc_was_cancelled: bool
    awaiter: Waker
    done: bool
    has_result: bool
    has_exc: bool
    executor_owned: bool

    def __init__(self, frame: Own[Box[AsyncFrame[T]]]) -> None:
        self.frame = frame
        self.result = UninitArrayStorage[T, 1]()
        self.exc = None
        self.exc_was_cancelled = False
        self.awaiter = Waker()
        self.done = False
        self.has_result = False
        self.has_exc = False
        self.executor_owned = False

    def __del__(self) -> None:
        if self.has_result:
            self.result.take0()
            self.has_result = False

    # User-facing poll. Drives the frame for non-executor-owned tasks;
    # for executor-owned tasks, parks (the executor's poll_any drives).
    def __poll__(self, w: Waker) -> Own[Poll[T]]:
        if self.done:
            if self.has_exc:
                if self.exc_was_cancelled:
                    raise CancelledError()
                exc = self.exc
                if exc is None:
                    raise RuntimeError("Task: done state inconsistent")
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
        except CancelledError:
            self.done = True
            self.has_exc = True
            self.exc_was_cancelled = True
            raise
        except BaseException as e:
            self.done = True
            self.has_exc = True
            # Explicit copy: acknowledges that `e`'s dynamic type is
            # sliced into the BaseException slot (separate concern from
            # whether we want a copy; tracked in BUGS.md).
            self.exc = copy(e)
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
        except CancelledError:
            self.done = True
            self.has_exc = True
            self.exc_was_cancelled = True
            self.awaiter.wake()
            return True
        except BaseException as e:
            self.done = True
            self.has_exc = True
            # Explicit copy: acknowledges that `e`'s dynamic type is
            # sliced into the BaseException slot (separate concern from
            # whether we want a copy; tracked in BUGS.md).
            self.exc = copy(e)
            self.awaiter.wake()
            return True

    def cancel_any(self) -> None:
        # Self-mutation token so auto-readonly inference doesn't mark
        # this method const (which would block the non-const
        # frame.cancel() call below via the @dynamic Box).
        self.done = self.done
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


@builtin_type("tpy.Task")
@nocopy
class Task[T]:
    """Type-erased async task. Holds a `Rc[TaskState[T]]` shared with
    the executor's slot table for spawned tasks.

    Claims the user-facing qname `tpy.Task` via `@builtin_type`. The
    rest is regular @nocopy generic-class machinery: the C++ name
    (`::tpystd::asyncio::_executor::Task<T>`) is derived from this
    module's `# tpy: cpp_namespace` directive through
    `NominalType._fallback_cpp_base_name`, and awaitability comes from
    the `__poll__(self, w: Waker) -> Poll[T]` method below being
    structurally matched by `_extract_awaitable_inner` in
    `tpyc/sema/expressions.py`.
    """

    _state: Rc[TaskState[T]]
    # _cancelled exists to defeat auto-readonly inference on cancel()
    # (same workaround as TaskState.cancel_any).
    _cancelled: bool

    def __init__(self, state: Own[Rc[TaskState[T]]]) -> None:
        self._state = state
        self._cancelled = False

    def __poll__(self, w: Waker) -> Own[Poll[T]]:
        return self._state.get().__poll__(w)

    def cancel(self) -> None:
        self._cancelled = True
        self._state.get().cancel_any()


# Wrap a concrete coro in `Box[AsyncFrame[T]]`. `decltype({0})`
# recovers the concrete CoroT at the call site so the Adapter
# specialization picks up the right inner type.
@cpp_template(
    "::tpystd::tplib::box::Box<::tpystd::asyncio::_executor::AsyncFrame<{T}>>("
    "std::make_unique<::tpy::Adapter<"
    "::tpystd::asyncio::_executor::AsyncFrame<{T}>, "
    "std::remove_cvref_t<decltype({0})>>>(std::move({0})))"
)
def _box_coro[T](coro: Own[Awaitable[T]]) -> Own[Box[AsyncFrame[T]]]: ...


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


# --- Current-executor global + ops bridge -------------------------------


# Single-process global tracking the running executor's opaque handle.
# v1 asyncio is single-executor per process; multi-threaded async (v3+)
# needs TPy thread-local module globals. See TODO.md for the cross-system
# TLS revert note (mirrors `executor_ops` in async.hpp).
_current_executor: ExecutorHandle = ExecutorHandle()


def _get_current_executor() -> ExecutorHandle:
    return _current_executor


def _set_current_executor(handle: ExecutorHandle) -> None:
    global _current_executor
    _current_executor = handle


def _clear_current_executor() -> None:
    global _current_executor
    _current_executor = ExecutorHandle()


# Clears the C++ ExecutorOps dispatch table so stale Wakers become
# silent no-ops in Waker::wake (the table's mark_runnable becomes null).
@native("tpy::clear_executor_ops")
def _clear_executor_ops() -> None: ...


def _executor_scope_teardown() -> None:
    _clear_executor_ops()
    _clear_current_executor()


# Construct a Waker stamped with this executor's handle + task id +
# generation. `Waker::wake` dispatches into mark_runnable via the
# ExecutorOps table.
def _make_waker(handle: ExecutorHandle, task_id: Int32,
                generation: Int32) -> Waker:
    return Waker(handle, task_id, generation)


# Dispatch into the current TPy `Executor`'s `spawn` method from a TPy
# context that only has the opaque `ExecutorHandle`. The static_cast
# uses the fully qualified `::tpystd::asyncio::_executor::Executor`
# because the template body is inlined at every call site (in
# arbitrary modules' namespaces); a bare `Executor` would fail to
# resolve outside this module's compilation unit.
@cpp_template(
    "static_cast<::tpystd::asyncio::_executor::Executor*>({0}.ptr)->spawn({1})"
)
def _executor_spawn_via_handle(handle: ExecutorHandle,
                               box: Own[Box[AnyTask]]) -> Int32: ...


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
    before completion (see `Waker::wake` in
    `runtime/cpp/include/tpy/async.hpp`: late wakes whose generation
    no longer matches are silent no-ops).

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
class Executor:
    """v1 asyncio executor: runnable-queue + timer-heap driver.

    Spawned tasks live in indexed slots; `Waker.wake()` marks a slot
    runnable if the slot is still live and the generation matches.
    Timers store the parked task's waker so a fired timer wakes only
    the task that registered it.

    `Waker::wake()` dispatches into this executor's `mark_runnable`
    via the C++ ExecutorOps table populated by `__init__`. See
    `runtime/cpp/include/tpy/async.hpp` for the dispatch shape and
    `docs/ASYNC_PROGRESS.md` for the v1.1 port history.
    """

    slots: list[Slot]
    runnable_q: list[Int32]
    timer_heap: list[TimerEntry]

    def __init__(self) -> None:
        # Backstop for `asyncio.run`'s nested-loop check: a non-null
        # current_executor means a `_ExecutorScope` is already active.
        # Bare `Executor()` in unit tests is unaffected because those
        # tests never set the global.
        if not _get_current_executor().is_null():
            raise RuntimeError(
                "Executor: another executor is already running "
                "(nested asyncio.run or leaked _ExecutorScope)")
        self.slots = []
        self.runnable_q = []
        self.timer_heap = []
        _register_executor_ops_from(self)

    def register_timer(self, deadline: float, waker: Waker) -> None:
        heapq.heappush(self.timer_heap, TimerEntry(deadline, waker))

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
        waker = _make_waker(_self_handle(self), slot_id, gen)
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


# Erase the Executor reference to an opaque ExecutorHandle (void* under
# the hood). Used inside poll_slot to stamp Wakers with a back-pointer
# to this executor. Free function because @cpp_template method bodies
# aren't allowed on regular TPy classes; the C++ helper takes a
# templated reference so async.hpp doesn't need to know the generated
# Executor class name.
@cpp_template("::tpy::make_executor_handle({0})")
def _self_handle(executor: Executor) -> ExecutorHandle: ...


# Wire Waker::wake() into this Executor's mark_runnable via the
# thread-local ExecutorOps table. Idempotent for a fixed `ExecT`.
@cpp_template("::tpy::register_executor_ops_from({0})")
def _register_executor_ops_from(executor: Executor) -> None: ...


# Test-only Box[AnyTask] factory: builds a TaskState[T] without
# executor registration so tests can drive the executor directly.
def _make_any_task_for_test[T](coro: Own[Awaitable[T]]) -> Own[Box[AnyTask]]:
    task = make_executor_owned_task[T](coro)
    return task_to_any_box[T](task)


@nocopy
class _ExecutorScope:
    """RAII guard for the current-executor global + ExecutorOps dispatch
    table. Writes `executor`'s handle on construction; clears both on
    `__del__` so any Waker stamped against this executor that fires
    after teardown becomes a silent no-op rather than dispatching
    through a stale `void* exec`.
    """

    def __init__(self, executor: Executor) -> None:
        _set_current_executor(_self_handle(executor))

    def __del__(self) -> None:
        _executor_scope_teardown()
