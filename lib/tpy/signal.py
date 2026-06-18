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

from tpy import Int32
from _bindings import posix_signal

# Linux-stable signal numbers, hardcoded the way socket.py hardcodes AF_INET.
SIGINT: Final[Int32] = 2
SIGTERM: Final[Int32] = 15


def raise_signal(sig: Int32) -> None:
    """Send `sig` to the current process (CPython's `signal.raise_signal`)."""
    posix_signal.raise_signal(sig)
