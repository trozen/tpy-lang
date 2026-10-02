# tpy: include("<tpy/threading.hpp>")
# tpy: link("pthread")
"""OS-thread spawn/join -- V1 (Runnable-struct form).

`spawn` moves a `Send` task onto a fresh OS thread, runs its `run()` there,
and returns a `JoinHandle` whose `join()` blocks for the result (re-raising
any exception the task threw). The model is Rust's Send + move in Python
syntax -- not CPython threading (see docs/THREADING_DESIGN.md).

Both the task and the result `R` cross the thread boundary, so both must be
`Send`: the task via the `Send[Own[T]]` param wrapper (rejected at the call
site with a why-not chain), `R` via the `R: Send` bound.

`spawn(task)` infers both type args: `T` through the `Send[]` wrapper and `R`
from the task's `run()` return type (associated-type inference through the
`ThreadTask[R]` bound). The explicit `spawn[R, T](task)` form still works but
is not valid CPython (a generic function is not subscriptable at runtime).
"""
from typing import Protocol
from tpy import Own, Send, nocopy
from tpy.extern import native
from _bindings import posix_signal


class ThreadTask[R](Protocol):
    def run(self) -> R: ...


# Raw C++ handle owning the std::thread + std::future. Move-only; its
# destructor only detaches (safe teardown). The consume bookkeeping and the
# interruptible wait live in the TPy `JoinHandle` below.
@native("tpy::JoinHandle")
@nocopy
class _RawJoin[R]:
    # True once the task has finished; waits at most `seconds` for it.
    def wait_for(self, seconds: float) -> bool: ...
    def join(self) -> Own[R]: ...
    def detach(self) -> None: ...


# Send is enforced at the user-facing `spawn` boundary; the internal handoff
# takes a plain Own[T] (already Send-checked) so the forward moves the task.
@native("tpy::spawn_thread")
def _spawn_native[R: Send, T: ThreadTask[R]](task: Own[T]) -> Own[_RawJoin[R]]: ...


@nocopy
class JoinHandle[R]:
    """Handle to a spawned thread. join() or detach() it at most once;
    dropping it unconsumed detaches the thread, as Rust's JoinHandle does:
    the thread runs on and its result or exception is discarded.

    On the main thread join() is interruptible: a Ctrl-C raises
    KeyboardInterrupt and leaves the handle unconsumed, so join() can be
    called again (a plain join in a `--no-signals` build)."""
    _raw: _RawJoin[R]
    _consumed: bool

    def __init__(self, raw: Own[_RawJoin[R]]) -> None:
        self._raw = raw
        self._consumed = False

    # `-> Own[R]`, not `-> R`: the result is moved out of the future. A bare
    # `-> R` is the generic BORROW convention, which binds no rvalue -- and a
    # borrow would point into a task the worker thread already destroyed.
    def join(self) -> Own[R]:
        if self._consumed:
            raise RuntimeError("JoinHandle.join(): handle already consumed")
        if posix_signal.interrupt_armed():
            # A thread's end is not an fd to wait on together with the Ctrl-C
            # wake fd, so the wait goes in slices. A KeyboardInterrupt out of
            # it leaves the handle unconsumed.
            while not self._raw.wait_for(0.1):
                posix_signal.check_signals()
            # A Ctrl-C during the last slice is still this join's to deliver.
            posix_signal.check_signals()
        self._consumed = True
        return self._raw.join()

    def detach(self) -> None:
        if self._consumed:
            raise RuntimeError("JoinHandle.detach(): handle already consumed")
        self._consumed = True
        self._raw.detach()

    def __del__(self) -> None:
        if not self._consumed:
            self._raw.detach()


def spawn[R: Send, T: ThreadTask[R]](task: Send[Own[T]]) -> Own[JoinHandle[R]]:
    return JoinHandle[R](_spawn_native[R, T](task))
