# Waker dispatch into the TPy Executor. Validates that an externally-
# held Waker pointing at a parked slot, when wake()'d from outside the
# executor's run loop, correctly re-schedules the slot through the
# @dynamic Awaker vtable into Executor.mark_runnable.
from asyncio._executor import (
    Executor,
    _make_any_task_for_test,
    _make_waker,
)
from time import monotonic
from tpy import UInt32, Own
from tpy.coro import Poll, Waker, poll_pending, poll_ready_none


class NeverComplete:
    """Always-Pending awaitable -- keeps a slot parked so we can re-wake
    it externally to verify the dispatch path."""
    __cancel_pending: bool

    def __init__(self) -> None:
        self.__cancel_pending = False

    def __poll__(self, waker: Waker) -> Own[Poll[None]]:
        return poll_pending()

    def cancel(self) -> None:
        self.__cancel_pending = True


class CountdownThenReady:
    """Returns Pending for the first N polls, then Ready. Used to
    confirm that the wake -> poll -> wake -> poll cycle drives a task
    to completion when wake() goes through the ops table."""
    remaining: UInt32
    __cancel_pending: bool

    def __init__(self, n: UInt32) -> None:
        self.remaining = n
        self.__cancel_pending = False

    def __poll__(self, waker: Waker) -> Own[Poll[None]]:
        if self.remaining == UInt32(0):
            return poll_ready_none()
        self.remaining -= UInt32(1)
        return poll_pending[None]()

    def cancel(self) -> None:
        self.__cancel_pending = True


def test_external_wake() -> None:
    e = Executor()
    sid = e.spawn(_make_any_task_for_test(NeverComplete()))
    e.drain_runnable()
    print("parked, runnable_q:", len(e.runnable_q))
    print("slot done:", e.slot_done(sid))

    # Externally wake the parked slot via Waker.wake() -- this routes
    # through the C++ ops table back into Executor.mark_runnable.
    w = _make_waker(e, sid, 0)
    w.wake()
    print("after wake, runnable_q:", len(e.runnable_q))
    e.drain_runnable()
    print("re-parked, runnable_q:", len(e.runnable_q))


def test_stale_generation_wake() -> None:
    # A Waker for a slot whose generation has moved on is silently
    # dropped: mark_runnable rejects on generation mismatch.
    e = Executor()
    sid = e.spawn(_make_any_task_for_test(NeverComplete()))
    e.drain_runnable()
    # Fabricate a waker with a wrong (future) generation.
    stale = _make_waker(e, sid, 99)
    stale.wake()
    print("stale wake runnable_q:", len(e.runnable_q))


def test_timer_drives_to_completion() -> None:
    # Register a past-deadline timer paired with the slot's waker. The
    # run loop should pop the timer, route .wake() through the ops
    # table, re-poll the slot, and the slot completes after N polls.
    e = Executor()
    sid = e.spawn(_make_any_task_for_test(CountdownThenReady(UInt32(3))))
    # First drain handles the initial poll (decrements to 2).
    e.drain_runnable()
    print("after first poll, slot done:", e.slot_done(sid))
    # Loop: each iteration registers a past-deadline timer; wait_for_event
    # pops it and wakes the slot; drain_runnable polls it.
    while not e.slot_done(sid):
        w = _make_waker(e, sid, 0)
        e.register_timer(monotonic() - 0.5, w)
        e.wait_for_event()
        e.drain_runnable()
    print("countdown finished, slot done:", e.slot_done(sid))


def main() -> None:
    test_external_wake()
    print("---")
    test_stale_generation_wake()
    print("---")
    test_timer_drives_to_completion()


main()
