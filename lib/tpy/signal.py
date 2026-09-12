# tpy: cpp_namespace("tpystd::signal")
"""Minimal `signal` module -- `raise_signal` plus the SIG* numbers asyncio's
graceful shutdown cares about.

A thin pure-TPy wrapper over the `posix_signal` binding. Under CPython
`import signal` resolves to the real stdlib module (same `raise_signal` /
`SIGINT` / `SIGTERM` surface), so the same source compiles and runs both ways.

Caveat: `raise_signal(SIGINT)` only behaves the same across runtimes *inside*
`asyncio.run`, where TPy installs a SIGINT handler. Outside it, TPy has no
SIGINT handler so the default action (terminate) fires, whereas CPython always
turns SIGINT into a catchable `KeyboardInterrupt`. The asyncio SIGINT handler
lives in the executor's signal scope, not here.
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
    """Send `sig` to the current process (CPython's `signal.raise_signal`)."""
    posix_signal.raise_signal(sig)
