# tpy: cpp_namespace("tpystd::asyncio::_executor")
# tpy: include("<tpy/async.hpp>")
# tpy: include("<tpy/stdlib/time.hpp>")
"""Internal asyncio scaffolding -- the TPy executor + the bindings it
needs to dispatch into the C++ runtime. Not part of the public asyncio
API; imported only by `lib/tpy/asyncio/` modules. See
`docs/ASYNC_PROGRESS.md` for the v1.1 port history.

Compiler bindings to the C++ runtime:

  * `ExecutorHandle` -- opaque handle to the running executor's
    `thread_local void*`. Default-constructed handles are null.
  * `_get_current_executor` / `_set_current_executor` /
    `_clear_current_executor` -- thin getters/setters around the
    thread-local, used by `_ExecutorScope` for asyncio.run setup /
    teardown.
  * `_executor_scope_teardown` -- atomically clears both the
    thread-local and the ExecutorOps dispatch table so stale Wakers
    become silent no-ops.
  * `AnyTaskBox` -- type-erased owning slot entry; `list[AnyTaskBox]`
    is the executor's task table. See the C++-side TODO in
    `runtime/cpp/include/tpy/async.hpp` for when this wrapper goes
    away (blocked on shared-ownership smart pointer in TPy).

TPy-side classes:

  * `Slot` -- the per-task entry. Wraps an `AnyTaskBox` with a
    generation counter (advanced on slot completion to invalidate
    stale wakers) and a runnable flag.
  * `Executor` -- the v1 asyncio executor; runnable-queue + timer-heap
    driver.
  * `_ExecutorScope` -- RAII guard around `current_executor` +
    `executor_ops`.
"""
import heapq

from time import monotonic, sleep_until_steady
from tpy import Int32, Own, ValueType, nocopy, readonly
from tpy.extern import cpp_template, native
from tpy.coro import Awaitable, Waker


@native("tpy::ExecutorHandle")
class ExecutorHandle(ValueType):
    """Opaque handle to the running executor's thread-local pointer.

    POD value type (8-byte `void* ptr` C++ side), default-constructs
    to null. The TPy executor port (Phase 2/3) stores its own pointer
    here via `_set_current_executor` and reads it back via
    `_get_current_executor`. Awaitables that need timer access today
    consult the thread-local directly through the
    `executor_register_timer_seconds` bridge, not through this handle.
    """
    def __init__(self) -> None: ...

    @readonly
    def is_null(self) -> bool: ...


@native("tpy::current_executor_get")
def _get_current_executor() -> ExecutorHandle: ...


@native("tpy::current_executor_set")
def _set_current_executor(handle: ExecutorHandle) -> None: ...


@native("tpy::current_executor_clear")
def _clear_current_executor() -> None: ...


@native("tpy::executor_scope_teardown")
def _executor_scope_teardown() -> None: ...


@native("tpy::AnyTaskBox")
@nocopy
class AnyTaskBox:
    """Type-erased owning slot entry for the executor's task table.

    Wraps a `shared_ptr<AnyTask>` on the C++ side so a spawned task can
    be shared between the user-facing `Task[T]` handle and the
    executor's slot table.

    TODO(async-v1.2): remove this wrapper once TPy gains a
    shared-ownership smart pointer; `TaskState[T]` moves to TPy then.
    """
    def __init__(self) -> None: ...

    @readonly
    def empty(self) -> bool: ...

    def reset(self) -> None: ...

    def poll_any(self, waker: Waker) -> bool: ...

    def cancel_any(self) -> None: ...


# Construct a Waker stamped with this executor's handle + task id +
# generation. The TPy executor's poll_slot calls this when handing a
# waker to a coroutine; `Waker::wake` dispatches into mark_runnable
# via the ExecutorOps table.
@native("tpy::make_waker")
def _make_waker(handle: ExecutorHandle, task_id: Int32,
                generation: Int32) -> Waker: ...


class TimerEntry(ValueType):
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

    Holds the AnyTaskBox driving a spawned task. The generation counter
    advances when the slot completes, invalidating any stale wakers
    that were handed out before completion (see `Waker::wake` in
    `runtime/cpp/include/tpy/async.hpp`: late wakes whose generation no
    longer matches are silent no-ops).

    `runnable` mirrors the slot's presence in the executor's runnable
    deque: set true when `mark_runnable` adds the slot's id to the
    queue, cleared when the executor pops it to poll.
    """

    box: AnyTaskBox
    generation: Int32
    runnable: bool

    def __init__(self) -> None:
        self.box = AnyTaskBox()
        self.generation = 0
        self.runnable = False

    @readonly
    def is_done(self) -> bool:
        return self.box.empty()


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
        self.slots = []
        self.runnable_q = []
        self.timer_heap = []
        _register_executor_ops_from(self)

    def register_timer(self, deadline: float, waker: Waker) -> None:
        heapq.heappush(self.timer_heap, TimerEntry(deadline, waker))

    def spawn(self, box: Own[AnyTaskBox]) -> Int32:
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
        if self.slots[slot_id].box.poll_any(waker):
            self.slots[slot_id].box.reset()
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
                self.slots[i].box.cancel_any()
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


# Test-only AnyTaskBox factory: builds a TaskState<T> without executor
# registration so tests can drive the executor directly.
@cpp_template("::tpy::make_any_task_for_test<{T}>({0})")
def _make_any_task_for_test[T](coro: Awaitable[T]) -> Own[AnyTaskBox]: ...


@nocopy
class _ExecutorScope:
    """RAII guard for the current-executor thread-local + ExecutorOps
    dispatch table. Writes `executor`'s handle on construction; clears
    both the thread-local and the ops table on `__del__` so any Waker
    stamped against this executor that fires after teardown becomes a
    silent no-op rather than dispatching through a stale `void* exec`.

    v1 asyncio.run doesn't support nesting (the caller already verified
    the thread-local was null before constructing the scope), so the
    saved-prev is always null and the clear-on-teardown is equivalent
    to a save/restore. Nestable-runtime support could grow a
    `_prev: ExecutorHandle` field freely now -- ExecutorHandle declares
    `ValueType` and picks up an `is_value_type<ExecutorHandle>` spec via
    the native-records emission pass.
    """

    def __init__(self, executor: Executor) -> None:
        _set_current_executor(_self_handle(executor))

    def __del__(self) -> None:
        _executor_scope_teardown()
