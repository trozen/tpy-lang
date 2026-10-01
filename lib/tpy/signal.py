# tpy: cpp_namespace("tpystd::signal")
"""Minimal `signal` module -- `raise_signal` plus the SIGINT / SIGTERM numbers.

A thin pure-TPy wrapper over the `posix_signal` binding. Under CPython
`import signal` resolves to the real stdlib module (same `raise_signal` /
`SIGINT` / `SIGTERM` surface), so the same source compiles and runs both ways.

SIGINT behaves as in CPython: a standalone program turns it into
`KeyboardInterrupt` on the main thread (at the next interruptible operation,
immediately for `raise_signal`), and inside `asyncio.run` it cancels the root
task first. The handler is the runtime's process-wide SIGINT layer
(runtime/cpp/src/stdlib/signal_impl.cpp), installed at startup unless SIGINT
was inherited as ignored. There is no `signal.signal` to install handlers of
your own.
"""
from typing import Final

from tpy import int32
from tpy.extern import native_global
from _bindings import posix_signal

# Sourced from <signal.h> via `tpy_const_*` extern symbols (see signal_impl.cpp),
# the way socket.py sources SO_*/AF_INET6. A plain `Final[int32] = 2` would emit
# `inline constexpr int32_t SIGINT = ...`, which the libc SIGINT macro (in scope
# in every generated TU on macOS) rewrites into a malformed declaration.
SIGINT: Final[int32] = native_global("tpy_const_sigint", binding="C")
SIGTERM: Final[int32] = native_global("tpy_const_sigterm", binding="C")


def raise_signal(sig: int32) -> None:
    """Send `sig` to the current process (CPython's `signal.raise_signal`).
    On the main thread a SIGINT raises KeyboardInterrupt before this returns,
    as in CPython (inside `asyncio.run` it cancels the root task instead)."""
    posix_signal.raise_signal(sig)
    posix_signal.check_interrupt()
