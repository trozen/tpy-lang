# tpy: native_module
# tpy: cpp_namespace("tpystd::_bindings::posix_epoll")
# tpy: include("<tpy/stdlib/epoll_h.hpp>")
"""Raw epoll C bindings (Linux).

Thin @native layer over the three flat `tpy_epoll_*` helpers in
runtime/cpp/src/stdlib/epoll_impl.cpp, which wrap libc's
epoll_create1 / epoll_ctl / epoll_wait. The packed `struct epoll_event`
and the `EPOLL*` macros never cross into TPy land -- the helpers take
flat scalars + parallel out-arrays instead (mirrors posix_socket's
tpy_resolve_ipv4 strategy).

Declaration-only and not for direct user import. The EPOLL* / EPOLL_CTL_*
wire values are hardcoded TPy-side by the consuming reactor, not here.

`# tpy: native_module` means no .cpp/.hpp is generated for this module;
its bindings resolve at link time to the tpy_epoll_* symbols.
"""

from tpy import Int32, UInt32, Ptr
from tpy.extern import native


# Leading `::` makes the rename an absolute (global-scope) C++ name, so
# codegen does NOT qualify it with this module's cpp_namespace -- required
# to reference the `extern "C"` symbols from epoll_h.hpp. (A bare name,
# even with binding="C", is still namespace-qualified by codegen and would
# emit `::tpystd::_bindings::posix_epoll::tpy_epoll_*`, which does not
# exist.) Same convention as posix_socket.py's `::tpy_resolve_ipv4`.

@native("::tpy_epoll_create")
def epoll_create() -> Int32: ...

@native("::tpy_epoll_ctl")
def epoll_ctl(epfd: Int32, op: Int32, fd: Int32, events: UInt32) -> Int32: ...

@native("::tpy_epoll_wait")
def epoll_wait(epfd: Int32, out_fds: Ptr[Int32], out_events: Ptr[UInt32],
               maxevents: Int32, timeout_ms: Int32) -> Int32: ...
