# Phase 2.2/2.3 v1.x asyncio-port: TPy-side Executor class driven
# directly (no asyncio.run, no C++ Executor involvement). Validates
# spawn / drain_runnable / run_until / slot_done / has_live_tasks /
# register_timer / wait_for_event on a coroutine that returns
# immediately. The wake path (Waker::wake() dispatching into the TPy
# executor) is deferred to Phase 2.4; tests here use either coroutines
# that complete on first poll or directly-pushed runnable ids.
from asyncio._executor import (
    Executor,
    _make_any_task_for_test,
    _make_waker,
    _self_handle,
)
from time import monotonic
from tpy import CancelledError
from tpy.coro import Poll, Waker, poll_pending


class CancellableForever:
    """Always-Pending awaitable that respects __cancel_pending: on the
    next poll after cancel, raises CancelledError so the task finishes.
    Used to drive drain_spawned_with_cancel's live-task branch."""
    __cancel_pending: bool

    def __init__(self) -> None:
        self.__cancel_pending = False

    def __poll__(self, waker: Waker) -> Poll[None]:
        if self.__cancel_pending:
            raise CancelledError()
        return poll_pending[None]()


async def returns_value(x: int) -> int:
    return x + 100


async def void_coro() -> None:
    return None


def test_spawn_and_run() -> None:
    e = Executor()
    box = _make_any_task_for_test(returns_value(7))
    sid = e.spawn(box)
    print("spawn id:", sid)
    print("len slots:", len(e.slots))
    print("len runnable_q:", len(e.runnable_q))
    print("slot_done before drain:", e.slot_done(sid))

    e.run_until(sid)
    print("slot_done after run:", e.slot_done(sid))
    # No live tasks remain (the main coro is done; nothing to skip).
    print("has_live skip=ff:", e.has_live_tasks(-1))


def test_multiple_spawns() -> None:
    e = Executor()
    a = e.spawn(_make_any_task_for_test(void_coro()))
    b = e.spawn(_make_any_task_for_test(returns_value(42)))
    print("two slots:", len(e.slots))
    print("two runnable:", len(e.runnable_q))
    polled = e.drain_runnable()
    print("drained:", polled)
    print("a done:", e.slot_done(a))
    print("b done:", e.slot_done(b))
    print("has_live skip a:", e.has_live_tasks(a))


def test_timer_fires_immediately() -> None:
    # Register a past-deadline timer; wait_for_event pops it.
    # The waker points at slot 0 generation 0, which doesn't exist
    # (no spawn), so mark_runnable inside wait_for_event no-ops on the
    # out-of-range slot id. We're testing the timer-heap mechanics
    # only here.
    e = Executor()
    w = _make_waker(_self_handle(e), 0, 0)
    e.register_timer(monotonic() - 0.5, w)
    print("timer count before:", len(e.timer_heap))
    print("wait fired:", e.wait_for_event())
    print("timer count after:", len(e.timer_heap))


def test_drain_with_no_tasks() -> None:
    e = Executor()
    print("idle drain:", e.drain_runnable())
    e.drain_spawned_with_cancel(-1)
    print("idle drain after cancel:", e.drain_runnable())


def test_drain_cancels_live_task() -> None:
    # Live-task branch of drain_spawned_with_cancel: spawn a task that
    # parks forever, drain (it stays parked), then cancel-drain with a
    # skip_id that doesn't match. The task should observe __cancel_pending
    # on the next poll, raise CancelledError, and finish.
    e = Executor()
    sid = e.spawn(_make_any_task_for_test(CancellableForever()))
    e.drain_runnable()
    print("parked, slot done:", e.slot_done(sid))
    print("has live before cancel:", e.has_live_tasks(-1))
    e.drain_spawned_with_cancel(-1)
    print("after cancel, slot done:", e.slot_done(sid))
    print("has live after cancel:", e.has_live_tasks(-1))


def main() -> None:
    test_spawn_and_run()
    print("---")
    test_multiple_spawns()
    print("---")
    test_timer_fires_immediately()
    print("---")
    test_drain_with_no_tasks()
    print("---")
    test_drain_cancels_live_task()


main()
