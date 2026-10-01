# tpy: cpp_namespace("tpystd::_interrupt")
"""Ctrl-C delivery for the stdlib's blocking operations.

The SIGINT layer (runtime/cpp/src/stdlib/signal_impl.cpp) turns a Ctrl-C into
a pending flag plus a byte on a wake fd; KeyboardInterrupt is raised later, on
the thread that armed the layer, by whichever interruptible operation runs
next. The stdlib builds its fd waits from the pieces here (a wait that is
not on an fd, `tpy.thread`'s join, takes only the check, straight from the
binding):
`check()` where a Ctrl-C that is already pending must be delivered, and
`wait_readable` / `wait_writable` for a wait on an fd that a Ctrl-C also
ends. A timeout is one monotonic deadline for the whole operation
(`deadline_after`), so each retry waits only for the time left.

Internal: not for user import.
"""

from typing import Final
from tpy import int32
from _bindings import posix_signal
import time

# posix_signal.wait's results (tpy/interrupt.hpp's wait result codes). WAIT_ERROR
# leaves errno set for the caller's own OSError mapping.
READY: Final[int32] = 1
WAIT_ERROR: Final[int32] = -1
TIMED_OUT: Final[int32] = -2
_INTERRUPTED: Final[int32] = -3


def check() -> None:
    """Raise KeyboardInterrupt if a Ctrl-C is pending for this thread."""
    posix_signal.check_interrupt()


def deadline_after(timeout: float) -> float:
    """The monotonic time `timeout` seconds from now, or -1.0 (no deadline)
    for a negative `timeout`."""
    if timeout < 0.0:
        return -1.0
    return time.monotonic() + timeout


def remaining(deadline: float) -> float:
    """Seconds left until `deadline` (0.0 once it has passed), or -1.0 when
    there is no deadline."""
    if deadline < 0.0:
        return -1.0
    left = deadline - time.monotonic()
    return left if left > 0.0 else 0.0


def wait_readable(fd: int32, deadline: float) -> int32:
    """Wait until `fd` is readable or `deadline` passes: READY, TIMED_OUT or
    WAIT_ERROR. A Ctrl-C raises KeyboardInterrupt. An already expired
    deadline still polls once, so an fd that is ready counts as ready."""
    return _wait(fd, 0, deadline)


def wait_writable(fd: int32, deadline: float) -> int32:
    """`wait_readable` for writability."""
    return _wait(fd, 1, deadline)


def _wait(fd: int32, want_write: int32, deadline: float) -> int32:
    rc = posix_signal.wait(fd, want_write, remaining(deadline))
    if rc == _INTERRUPTED:
        raise KeyboardInterrupt()
    return rc
