# signal.signal handlers run on the main thread at the next check point and
# raise out of it (in a coroutine too). Never prints the frame (None in TPy).
import asyncio
import os
import signal
import sys
import time
import types
from signal import default_int_handler
from types import FrameType
from typing import Iterator

from tpy import int32
from tpy.thread import spawn


class Stop(Exception):
    pass


hits: int32 = 0


def count(signum: int, frame: FrameType | None) -> None:
    global hits
    hits += 1


def on_usr1(signum: int, frame: FrameType | None) -> None:
    print("handler: got", signum == signal.SIGUSR1)


def on_term(signum: int, frame: FrameType | None) -> None:
    sys.exit(128 + signum)


def on_hup(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt()


def on_int(signum: int, frame: FrameType | None) -> None:
    print("sigint: user handler ran")


def raises_stop(signum: int, frame: FrameType | None) -> None:
    raise Stop()


def second(signum: int, frame: FrameType | None) -> None:
    print("replace: second ran")


def first(signum: int, frame: FrameType | None) -> None:
    signal.signal(signal.SIGUSR2, second)
    print("replace: first ran and replaced itself")


# raise_signal: the handler runs before the call returns
def runs_before_return() -> None:
    signal.signal(signal.SIGUSR1, on_usr1)  # tpyc: ok
    signal.raise_signal(signal.SIGUSR1)  # tpyc: ok -- runs on_usr1 here
    print("handler: after raise_signal")


# sys.exit in a handler: SystemExit unwinds the finally, caught outside
def exit_in_handler() -> None:
    signal.signal(signal.SIGTERM, on_term)
    try:
        try:
            signal.raise_signal(signal.SIGTERM)  # tpyc: ok -- raises SystemExit
            print("exit: not reached")
        finally:
            print("exit: finally ran")
    except SystemExit as e:
        print("exit: code", e.code)


# a handler raising KeyboardInterrupt
def handler_raises_ki() -> None:
    signal.signal(signal.SIGHUP, on_hup)
    try:
        signal.raise_signal(signal.SIGHUP)  # tpyc: ok
        print("hup: not reached")
    except KeyboardInterrupt:
        print("hup: KeyboardInterrupt from the handler")


# a SIGINT handler replaces KeyboardInterrupt; default_int_handler restores it
def sigint_handler() -> None:
    signal.signal(signal.SIGINT, on_int)  # tpyc: ok
    signal.raise_signal(signal.SIGINT)
    print("sigint: no KeyboardInterrupt")
    signal.signal(signal.SIGINT, default_int_handler)  # tpyc: ok -- back to the default
    try:
        signal.raise_signal(signal.SIGINT)
        print("sigint: not reached")
    except KeyboardInterrupt:
        print("sigint: KeyboardInterrupt again")
    try:
        default_int_handler(2, None)  # tpyc: ok -- callable directly
    except KeyboardInterrupt:
        print("sigint: default_int_handler raises")


# os.kill of this process is a check point too
def kill_self() -> None:
    global hits
    hits = 0
    signal.signal(signal.SIGUSR2, count)
    os.kill(os.getpid(), signal.SIGUSR2)  # tpyc: ok -- count runs before it returns
    print("kill: hits", hits)


# a handler that replaces itself keeps running to its end
def replaces_itself() -> None:
    signal.signal(signal.SIGUSR2, first)
    signal.raise_signal(signal.SIGUSR2)
    signal.raise_signal(signal.SIGUSR2)


class Dropper:
    def __del__(self) -> None:
        signal.raise_signal(signal.SIGUSR1)


# a __del__ that sends a signal: the handler has run once the caller goes on
def after_del() -> None:
    global hits
    hits = 0
    signal.signal(signal.SIGUSR1, count)
    d = Dropper()
    del d  # tpyc: ok
    print("del: dropped")
    print("del: hits", hits)


def gen() -> Iterator[int32]:
    try:
        yield 1
        signal.raise_signal(signal.SIGUSR2)  # tpyc: ok -- raises Stop in the frame
        yield 2
    finally:
        print("generator: finally ran")


# a handler raising inside a generator body
def in_generator() -> None:
    signal.signal(signal.SIGUSR2, raises_stop)
    try:
        for n in gen():
            print("generator: got", n)
    except Stop:
        print("generator: Stop out of the loop")


async def background() -> None:
    try:
        await asyncio.sleep(10.0)
    finally:
        print("async: background cleanup")


async def root() -> int32:
    t = asyncio.create_task(background())
    await asyncio.sleep(0.01)
    try:
        signal.raise_signal(signal.SIGUSR2)  # tpyc: ok -- raises_stop raises Stop in the root
        await asyncio.sleep(10.0)
    finally:
        print("async: root cleanup")
    await t
    return 1


# asyncio.run: a handler's exception the root does not catch leaves the run
# after task cleanup
def in_asyncio() -> None:
    signal.signal(signal.SIGUSR2, raises_stop)
    try:
        r = asyncio.run(root())
        print("async: result (WRONG)", r)
    except Stop:
        print("async: Stop out of the run")


async def catches() -> int32:
    try:
        signal.raise_signal(signal.SIGUSR2)  # tpyc: ok -- raises_stop runs in this coroutine
        print("async catch: not reached")
    except Stop:
        print("async catch: caught Stop in the coroutine")
    await asyncio.sleep(0.01)
    return 2


# asyncio.run: a coroutine catches its handler's exception
def in_asyncio_catch() -> None:
    signal.signal(signal.SIGUSR2, raises_stop)
    r = asyncio.run(catches())
    print("async catch: result", r)


async def counts() -> int32:
    signal.raise_signal(signal.SIGUSR1)  # tpyc: ok -- count runs and returns
    await asyncio.sleep(0.01)
    return hits


# asyncio.run: a handler that returns lets the run finish with its result
def in_asyncio_returns() -> None:
    global hits
    hits = 0
    signal.signal(signal.SIGUSR1, count)
    r = asyncio.run(counts())
    print("async returns: result", r)


async def sigint_twice() -> int32:
    signal.raise_signal(signal.SIGINT)  # tpyc: ok -- the user handler runs here
    signal.raise_signal(signal.SIGINT)  # tpyc: ok -- so this one is not a fatal second Ctrl-C
    await asyncio.sleep(0.01)
    return hits


# asyncio.run: a user SIGINT handler runs at each raise_signal, no await between
def in_asyncio_user_sigint() -> None:
    global hits
    hits = 0
    signal.signal(signal.SIGINT, count)
    r = asyncio.run(sigint_twice())
    signal.signal(signal.SIGINT, default_int_handler)
    print("async sigint: hits", r)


def on_int_run(signum: int, frame: FrameType | None) -> None:
    print("run sigint: user handler ran")


async def resets_sigint() -> int32:
    signal.signal(signal.SIGINT, on_int_run)  # tpyc: ok -- replaces asyncio.run's own handler
    signal.raise_signal(signal.SIGINT)
    signal.signal(signal.SIGINT, default_int_handler)
    try:
        signal.raise_signal(signal.SIGINT)  # tpyc: ok -- KeyboardInterrupt here, not the loop's cancel
        print("run sigint: not reached")
    except KeyboardInterrupt:
        print("run sigint: KeyboardInterrupt in the coroutine")
    await asyncio.sleep(0.01)
    return 1


# asyncio.run: a SIGINT handler set inside the run replaces the run's own for
# the rest of it, default_int_handler too
def in_asyncio_resets_sigint() -> None:
    r = asyncio.run(resets_sigint())
    print("run sigint: result", r)


def on_int_kept(signum: int, frame: FrameType | None) -> None:
    print("kept sigint: user handler ran")


async def cancelled_then_sets() -> int32:
    try:
        signal.raise_signal(signal.SIGINT)  # tpyc: ok -- asyncio.run's handler cancels the root here
        signal.signal(signal.SIGINT, on_int_kept)  # tpyc: ok -- too late for that Ctrl-C
        await asyncio.sleep(0.01)
        print("kept sigint: not reached")
    except asyncio.CancelledError:
        print("kept sigint: root cancelled")
        raise
    return 1


# asyncio.run: a Ctrl-C delivered in the coroutine cancels the root although a
# SIGINT handler is set before the next await, and the run leaves that
# handler in place when it ends (it restores only its own)
def in_asyncio_keeps_user_sigint() -> None:
    try:
        r = asyncio.run(cancelled_then_sets())
        print("kept sigint: result (WRONG)", r)
    except KeyboardInterrupt:
        print("kept sigint: KeyboardInterrupt out of run")
    signal.raise_signal(signal.SIGINT)  # tpyc: ok -- the handler set in the run runs
    signal.signal(signal.SIGINT, default_int_handler)


async def returns_in_step() -> int32:
    signal.raise_signal(signal.SIGINT)  # tpyc: ok -- cancels the root, which returns before suspending
    print("same step: after raise_signal")
    return 1


async def returns_in_step_finally() -> int32:
    try:
        signal.raise_signal(signal.SIGINT)
        print("same step finally: after raise_signal")
    finally:
        print("same step finally: finally ran")
    return 2


# asyncio.run: a root that returns in the step its Ctrl-C arrived in is
# cancelled all the same (CPython's Task._must_cancel): the value is dropped
def in_asyncio_same_step() -> None:
    try:
        r = asyncio.run(returns_in_step())
        print("same step: result (WRONG)", r)
    except KeyboardInterrupt:
        print("same step: KeyboardInterrupt out of run")
    try:
        r = asyncio.run(returns_in_step_finally())
        print("same step finally: result (WRONG)", r)
    except KeyboardInterrupt:
        print("same step finally: KeyboardInterrupt out of run")


async def quick() -> int32:
    return 3


async def awaits_after_ctrl_c() -> int32:
    signal.raise_signal(signal.SIGINT)
    try:
        v = await quick()  # tpyc: ok -- completes without suspending: not cancelled
        print("step awaits: quick ran", v)
        await asyncio.sleep(10.0)  # the next real suspension takes the cancel
        print("step awaits: not reached")
    except asyncio.CancelledError:
        print("step awaits: cancelled at the sleep")
        raise
    return 1


async def sleeps_zero_after_ctrl_c() -> int32:
    signal.raise_signal(signal.SIGINT)
    try:
        await asyncio.sleep(0)  # tpyc: ok -- suspends once, so the cancel lands here
        print("step sleep0: not reached")
    except asyncio.CancelledError:
        print("step sleep0: cancelled")
        raise
    return 1


# asyncio.run: a Ctrl-C delivered in the root's own step lands at its next
# real suspension, not at the next await
def in_asyncio_step_awaits() -> None:
    try:
        r = asyncio.run(awaits_after_ctrl_c())
        print("step awaits: result (WRONG)", r)
    except KeyboardInterrupt:
        print("step awaits: KeyboardInterrupt out of run")
    try:
        r = asyncio.run(sleeps_zero_after_ctrl_c())
        print("step sleep0: result (WRONG)", r)
    except KeyboardInterrupt:
        print("step sleep0: KeyboardInterrupt out of run")


async def interrupting_child() -> int32:
    signal.raise_signal(signal.SIGINT)  # tpyc: ok -- in the child's poll: cancels the root
    print("spawned: child after raise_signal")
    return 7


async def awaits_interrupting_child() -> int32:
    t = asyncio.create_task(interrupting_child())
    try:
        r = await t
        print("spawned: root got (WRONG)", r)
    except asyncio.CancelledError:
        print("spawned: root cancelled")
        raise
    return 1


# asyncio.run: a Ctrl-C delivered at a check point in a spawned task cancels
# the root
def in_asyncio_spawned_ctrl_c() -> None:
    try:
        r = asyncio.run(awaits_interrupting_child())
        print("spawned: result (WRONG)", r)
    except KeyboardInterrupt:
        print("spawned: KeyboardInterrupt out of run")


async def late_child() -> None:
    print("late: child runs after the root returned")
    try:
        signal.raise_signal(signal.SIGINT)  # tpyc: ok -- the root is done: KeyboardInterrupt here
        print("late: not reached")
    except KeyboardInterrupt:
        print("late: KeyboardInterrupt in the child")
        raise


async def returns_before_child() -> int32:
    t = asyncio.create_task(late_child())
    print("late: root returns")
    return 1


# asyncio.run: a Ctrl-C after the root finished raises KeyboardInterrupt where
# it is delivered (CPython's Runner._on_sigint), here a task polled after the
# root in the same loop pass
def in_asyncio_after_root() -> None:
    try:
        r = asyncio.run(returns_before_child())
        print("late: result (WRONG)", r)
    except KeyboardInterrupt:
        print("late: KeyboardInterrupt out of run")


async def drained_child() -> None:
    try:
        await asyncio.sleep(10.0)
    finally:
        print("drain: child cancelled by the shutdown")
        signal.raise_signal(signal.SIGINT)  # tpyc: ok -- default_int_handler is back: KeyboardInterrupt
        print("drain: not reached")


async def leaves_child() -> int32:
    t = asyncio.create_task(drained_child())
    await asyncio.sleep(0.01)
    return 2


# asyncio.run puts default_int_handler back before the shutdown drain (as
# CPython's Runner before close()), so a Ctrl-C there raises KeyboardInterrupt
def in_asyncio_drain() -> None:
    try:
        r = asyncio.run(leaves_child())
        print("drain: result (WRONG)", r)
    except KeyboardInterrupt:
        print("drain: KeyboardInterrupt out of run")


async def staged_event_waiter(e: asyncio.Event) -> None:
    try:
        await e.wait()
        print("staged: event waiter woke")
    finally:
        print("staged: event waiter finally")


async def staged_ctrl_c(e: asyncio.Event) -> None:
    await asyncio.sleep(0)
    time.sleep(0.02)  # blocks past the root's deadline
    await asyncio.sleep(0)  # the root's timer fires: its wake is staged
    print("staged: Ctrl-C, then set")
    signal.raise_signal(signal.SIGINT)  # tpyc: ok -- cancels the root, queued ahead of the waiter
    e.set()


async def staged_root() -> None:
    e = asyncio.Event()
    w = asyncio.create_task(staged_event_waiter(e))
    c = asyncio.create_task(staged_ctrl_c(e))
    try:
        await asyncio.sleep(0.005)
        print("staged: root woke (WRONG)")
    finally:
        print("staged: root finally")


# asyncio.run: a Ctrl-C cancelling a root whose timer wake is already staged
# runs the root where the cancel happened, before a task woken after it
def in_asyncio_staged_root() -> None:
    try:
        asyncio.run(staged_root())
        print("staged: returned (WRONG)")
    except KeyboardInterrupt:
        print("staged: KeyboardInterrupt out of run")


async def failing_task() -> int32:
    signal.raise_signal(signal.SIGUSR2)  # tpyc: ok -- raises_stop fails only this task
    print("task: not reached")
    return 5


async def awaits_task() -> int32:
    t = asyncio.create_task(failing_task())
    await asyncio.sleep(0.01)
    print("task: root still running")
    try:
        r = await t
        print("task: got (WRONG)", r)
    except Stop:
        print("task: awaiting re-raised Stop")
    return 1


# asyncio.run: a handler's ordinary exception in a spawned task fails that
# task only; awaiting it re-raises
def in_asyncio_task() -> None:
    signal.signal(signal.SIGUSR2, raises_stop)
    r = asyncio.run(awaits_task())
    print("task: result", r)


class Napper:
    def run(self) -> int32:
        time.sleep(0.2)
        return 7


# a live worker thread: the handler still runs on main before raise_signal returns
def with_worker() -> None:
    global hits
    hits = 0
    signal.signal(signal.SIGUSR1, count)
    h = spawn(Napper())
    signal.raise_signal(signal.SIGUSR1)  # tpyc: ok -- count runs here, on the main thread
    print("worker: hits before join", hits)
    print("worker: joined", h.join())


class Installer:
    def run(self) -> str:
        try:
            signal.signal(signal.SIGUSR1, count)  # tpyc: ok -- raises off the main thread
        except ValueError as e:
            return "ValueError: " + str(e)
        return "installed (WRONG)"


# signal.signal from a spawned thread
def from_thread() -> None:
    print("thread:", spawn(Installer()).join())


# SIGKILL cannot be caught; 0 and NSIG are out of range
def errors() -> None:
    try:
        signal.signal(signal.SIGKILL, count)  # tpyc: ok -- OSError(EINVAL)
    except OSError as e:
        print("errors: SIGKILL", e.errno, e)
    for sig in [0, -1, 1000]:
        try:
            signal.signal(sig, count)
        except ValueError as e:
            print("errors:", sig, e)
    try:
        types.FrameType()  # tpyc: ok -- no frame objects, as in CPython
    except TypeError as e:
        print("errors: FrameType", e)


def main() -> None:
    runs_before_return()
    exit_in_handler()
    handler_raises_ki()
    sigint_handler()
    kill_self()
    replaces_itself()
    after_del()
    in_generator()
    in_asyncio()
    in_asyncio_catch()
    in_asyncio_returns()
    in_asyncio_user_sigint()
    in_asyncio_resets_sigint()
    in_asyncio_keeps_user_sigint()
    in_asyncio_same_step()
    in_asyncio_step_awaits()
    in_asyncio_spawned_ctrl_c()
    in_asyncio_after_root()
    in_asyncio_drain()
    in_asyncio_staged_root()
    in_asyncio_task()
    with_worker()
    from_thread()
    errors()


main()
