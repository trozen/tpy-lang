"""CPython stub for tpy.thread: spawn/JoinHandle over threading.Thread.

Semantics mirror the TPy surface where CPython can express them: run() executes
on a real OS thread, join() blocks and returns the result or re-raises the
task's exception, detach() consumes without waiting, double-consume raises
RuntimeError. A dropped handle detaches in TPy; here the thread is a plain
non-daemon Thread, so CPython waits for it at exit where TPy does not.
"""
import threading
from typing import Generic, Protocol, TypeVar

R = TypeVar("R")


class ThreadTask(Protocol[R]):
    def run(self) -> R: ...


class JoinHandle(Generic[R]):
    def __init__(self, thread: threading.Thread, box: dict,
                 done: threading.Event) -> None:
        self._thread = thread
        self._box = box
        self._done = done
        self._consumed = False

    def join(self) -> R:
        if self._consumed:
            raise RuntimeError("JoinHandle.join(): handle already consumed")
        # An interrupted wait (KeyboardInterrupt) leaves the handle joinable,
        # as in TPy. The Event, not an interruptible Thread.join(): on 3.12 a
        # KeyboardInterrupt out of Thread.join() marks the thread stopped, so
        # a retried join() returns before the result is stored.
        self._done.wait()
        self._thread.join()
        self._consumed = True
        if "exc" in self._box:
            raise self._box["exc"]
        return self._box["result"]

    def detach(self) -> None:
        if self._consumed:
            raise RuntimeError("JoinHandle.detach(): handle already consumed")
        self._consumed = True


def spawn(task):
    box: dict = {}
    done = threading.Event()

    def _run() -> None:
        try:
            box["result"] = task.run()
        except BaseException as e:  # delivered at join(), like future::get()
            box["exc"] = e
        finally:
            done.set()

    t = threading.Thread(target=_run)
    t.start()
    return JoinHandle(t, box, done)
