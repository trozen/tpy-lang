"""CPython stub for tpy.thread: spawn/JoinHandle over threading.Thread.

Semantics mirror the TPy surface where CPython can express them: run() executes
on a real OS thread, join() blocks and returns the result or re-raises the
task's exception, detach() consumes without waiting, double-consume raises
RuntimeError. The abort-on-unconsumed-drop panic is not reproduced (panic tests
are no_cpython).
"""
import threading
from typing import Generic, Protocol, TypeVar

R = TypeVar("R")


class ThreadTask(Protocol[R]):
    def run(self) -> R: ...


class JoinHandle(Generic[R]):
    def __init__(self, thread: threading.Thread, box: dict) -> None:
        self._thread = thread
        self._box = box
        self._consumed = False

    def join(self) -> R:
        if self._consumed:
            raise RuntimeError("JoinHandle.join(): handle already consumed")
        self._consumed = True
        self._thread.join()
        if "exc" in self._box:
            raise self._box["exc"]
        return self._box["result"]

    def detach(self) -> None:
        if self._consumed:
            raise RuntimeError("JoinHandle.detach(): handle already consumed")
        self._consumed = True


def spawn(task):
    box: dict = {}

    def _run() -> None:
        try:
            box["result"] = task.run()
        except BaseException as e:  # delivered at join(), like future::get()
            box["exc"] = e

    t = threading.Thread(target=_run)
    t.start()
    return JoinHandle(t, box)
