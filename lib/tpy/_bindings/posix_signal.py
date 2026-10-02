# tpy: native_module
# tpy: cpp_namespace("tpystd::_bindings::posix_signal")
# tpy: include("<tpy/stdlib/signal_h.hpp>")
"""Raw signal C bindings.

Thin @native layer over the flat helpers in
runtime/cpp/src/stdlib/signal_impl.cpp, the process-wide SIGINT layer that
turns Ctrl-C into KeyboardInterrupt and wakes asyncio's reactor. The
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

@native("::tpy_interrupt_async_consume")
def async_consume() -> int32: ...

@native("::tpy_signal_raise")
def raise_signal(sig: int32) -> int32: ...

# Raises KeyboardInterrupt when a Ctrl-C is pending for the calling thread
# (tpy/core.hpp). Empty in a `--no-signals` build.
@native("::tpy::check_signals", checks_signals=True)
def check_signals() -> None: ...

# True while the SIGINT layer is armed (tpy/core.hpp); never in a
# `--no-signals` build.
@native("::tpy::interrupt_armed")
def interrupt_armed() -> bool: ...

# Marks a Ctrl-C pending without consuming it, as the SIGINT handler does;
# the next check point raises it (the embedding API's entry point,
# interrupt.hpp). A `--no-signals` build has no such symbol, so a call there
# fails to compile, like the host's `tpy::request_interrupt()`.
@native("::tpy_request_interrupt")
def request_interrupt() -> None: ...

# Wait for `fd` (readable, or writable when `want_write` != 0) with a timeout
# in seconds (< 0: none), woken by a Ctrl-C on the interrupt target thread.
# Returns 1 ready, -1 error (errno set), -2 timed out, -3 interrupted.
@native("::tpy_interrupt_wait", checks_signals=True)
def wait(fd: int32, want_write: int32, timeout: float) -> int32: ...
