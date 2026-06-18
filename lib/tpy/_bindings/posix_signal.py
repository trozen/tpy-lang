# tpy: native_module
# tpy: cpp_namespace("tpystd::_bindings::posix_signal")
# tpy: include("<tpy/stdlib/signal_h.hpp>")
"""Raw signal C bindings (Linux).

Thin @native layer over the flat `tpy_signal_*` helpers in
runtime/cpp/src/stdlib/signal_impl.cpp, which wrap sigaction + eventfd for
asyncio's graceful-shutdown support. The `<signal.h>` / `<sys/eventfd.h>`
macros and struct layouts never cross into TPy land -- the helpers take flat
scalars (mirrors posix_epoll's tpy_epoll_* strategy).

Declaration-only and not for direct user import; consumed by the asyncio
executor's signal scope. The `signal` stdlib module exposes the user-facing
`raise_signal` + SIG* constants on top of `tpy_signal_raise`.

`# tpy: native_module` means no .cpp/.hpp is generated for this module; its
bindings resolve at link time to the tpy_signal_* symbols.
"""

from tpy import Int32
from tpy.extern import native


# Leading `::` makes the rename an absolute (global-scope) C++ name so codegen
# does NOT qualify it with this module's cpp_namespace -- required to reference
# the `extern "C"` symbols from signal_h.hpp. Same convention as posix_epoll.py.

@native("::tpy_signal_install_shutdown")
def install_shutdown() -> Int32: ...

@native("::tpy_signal_restore")
def restore() -> None: ...

@native("::tpy_signal_consume")
def consume() -> Int32: ...

@native("::tpy_signal_raise")
def raise_signal(sig: Int32) -> Int32: ...
