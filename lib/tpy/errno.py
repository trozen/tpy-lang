# tpy: cpp_namespace("tpystd::errno_mod")
"""Minimal `errno` module: the constants TPy's own stdlib maps to exception
subclasses, for comparing `OSError.errno` in user code (the CPython idiom
`e.errno == errno.ECONNREFUSED`).

Values are read from the platform's <errno.h> via the same `tpy_const_*`
native globals the socket module uses (Linux and macOS/BSD diverge on
several), so comparisons are host-correct. Grow this list as more constants
gain a consumer; CPython's full table is large and mostly unused.
"""

from typing import Final
from tpy import int32
from tpy.extern import native_global

# Network-domain constants (defined in socket_impl.cpp).
EAGAIN:       Final[int32] = native_global("tpy_const_eagain", binding="C")
# EWOULDBLOCK == EAGAIN on Linux and macOS/BSD (POSIX allows them to differ,
# but no supported platform does).
EWOULDBLOCK:  Final[int32] = native_global("tpy_const_eagain", binding="C")
EINPROGRESS:  Final[int32] = native_global("tpy_const_einprogress", binding="C")
EPIPE:        Final[int32] = native_global("tpy_const_epipe", binding="C")
ECONNRESET:   Final[int32] = native_global("tpy_const_econnreset", binding="C")
ECONNREFUSED: Final[int32] = native_global("tpy_const_econnrefused", binding="C")
ECONNABORTED: Final[int32] = native_global("tpy_const_econnaborted", binding="C")

# File-domain constants (defined in os_impl.cpp).
ENOENT:       Final[int32] = native_global("tpy_const_enoent", binding="C")
EEXIST:       Final[int32] = native_global("tpy_const_eexist", binding="C")
EACCES:       Final[int32] = native_global("tpy_const_eacces", binding="C")
EPERM:        Final[int32] = native_global("tpy_const_eperm", binding="C")
EISDIR:       Final[int32] = native_global("tpy_const_eisdir", binding="C")
ENOTDIR:      Final[int32] = native_global("tpy_const_enotdir", binding="C")
EBADF:        Final[int32] = native_global("tpy_const_ebadf", binding="C")
ETIMEDOUT:    Final[int32] = native_global("tpy_const_etimedout", binding="C")
