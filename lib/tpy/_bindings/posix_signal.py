# tpy: native_module
# tpy: cpp_namespace("tpystd::_bindings::posix_signal")
# tpy: include("<tpy/stdlib/signal_h.hpp>")
"""Raw signal C bindings.

Thin @native layer over the flat helpers in
runtime/cpp/src/stdlib/signal_impl.cpp, the process-wide signal layer that
turns Ctrl-C into KeyboardInterrupt, runs `signal.signal` handlers at check
points and wakes asyncio's reactor. The
`<signal.h>` / `<sys/eventfd.h>` macros and struct layouts never cross into
TPy land -- the helpers take flat scalars (mirrors posix_epoll's tpy_epoll_*
strategy).

Declaration-only and not for direct user import.

`# tpy: native_module` means no .cpp/.hpp is generated for this module; its
bindings resolve at link time to the signal_impl.cpp symbols.
"""

from tpy import int32
from tpy.extern import native


# Leading `::` makes the rename an absolute (global-scope) C++ name so codegen
# does NOT qualify it with this module's cpp_namespace -- required to reference
# the `extern "C"` symbols from signal_h.hpp. Same convention as posix_epoll.py.

@native("::tpy_interrupt_async_begin")
def async_begin() -> int32: ...

@native("::tpy_interrupt_async_end")
def async_end() -> None: ...

# Runs the pending signals' handlers from asyncio's loop; a handler's
# exception propagates. `after_wait` != 0 right after a reactor wait the wake
# fd fired on.
@native("::tpy_interrupt_async_deliver", checks_signals=True)
def async_deliver(after_wait: int32) -> None: ...

@native("::tpy_signal_raise")
def raise_signal(sig: int32) -> int32: ...

# Runs what the signals pending for the calling thread ask for: raises
# KeyboardInterrupt for a Ctrl-C, calls `signal.signal` handlers
# (tpy/core.hpp). Empty in a `--no-signals` build.
@native("::tpy::check_signals", checks_signals=True)
def check_signals() -> None: ...

# True while the signal layer is armed (tpy/core.hpp); never in a
# `--no-signals` build.
@native("::tpy::interrupt_armed")
def interrupt_armed() -> bool: ...

# True where a signal would be delivered on the calling thread right now: the
# interrupt target thread outside a deferral scope, inside asyncio.run too
# (tpy/core.hpp). Never in a `--no-signals` build.
@native("::tpy::signals_deliverable")
def deliverable() -> bool: ...

# Marks a Ctrl-C pending without consuming it, as the SIGINT handler does;
# the next check point raises it (the embedding API's entry point,
# interrupt.hpp). A `--no-signals` build has no such symbol, so a call there
# fails to compile, like the host's `tpy::request_interrupt()`.
@native("::tpy_request_interrupt")
def request_interrupt() -> None: ...

# Marks `sig` pending as its C-level handler would, without sending it; the
# next check point delivers it. Lets a test pend several signals before one
# check point. Absent from a `--no-signals` build, like request_interrupt.
@native("::tpy_signal_request")
def request_signal(sig: int32) -> None: ...

# Wait for `fd` (readable, or writable when `want_write` != 0) with a timeout
# in seconds (< 0: none). On the interrupt target thread a signal arriving
# meanwhile is delivered inside the wait (a Ctrl-C raises KeyboardInterrupt
# out of it), and a handler that returns lets the wait go on to its
# deadline. Returns 1 ready, -1 error (errno set), -2 timed out.
@native("::tpy_interrupt_wait", checks_signals=True)
def wait(fd: int32, want_write: int32, timeout: float) -> int32: ...

# Installs the runtime's C-level handler for `sig` with `kind` (signal_h.hpp's
# kinds), raising signal.signal's CPython errors.
@native("::tpy_signal_set")
def set_handler(sig: int32, kind: int32) -> None: ...

# `set_handler(SIGINT, kind)` for asyncio.run's own SIGINT handler, also on a
# layer the run armed itself (signal_h.hpp).
@native("::tpy_signal_set_for_run")
def set_run_handler(kind: int32) -> None: ...

# The kind `sig` has on the calling thread (signal_h.hpp's kinds; none off
# the interrupt target thread).
@native("::tpy_signal_kind")
def handler_kind(sig: int32) -> int32: ...

# Publishes signal.py's dispatcher to the runtime (signal_h.hpp).
@native("::tpy_signal_enable_dispatch")
def enable_dispatch() -> None: ...
