# tpy: native_module
# tpy: cpp_namespace("tpystd::_bindings::posix_termios")
# tpy: include("<tpy/stdlib/termios_h.hpp>")
"""Raw termios bindings.

Thin @native layer over the two helpers in
runtime/cpp/src/stdlib/termios_impl.cpp. `struct termios` and the
<termios.h> macros never cross into TPy land: the helpers take and return
flat scalars plus the control-character bytes, and report failure as an
errno return (0 on success) so the public `termios` facade can raise
`termios.error` rather than an OSError.

Declaration-only and not for direct user import.
"""

from tpy import Own, int32, int64, readonly
from tpy.extern import native


# (errno, iflag, oflag, cflag, lflag, ispeed, ospeed); `cc` receives the
# control characters.
@native("tpy::stdlib::termios::tcgetattr_raw")
def tcgetattr_raw(fd: int64) -> tuple[
        int32, int64, int64, int64, int64, int64, int64, Own[bytearray]]: ...


@native("tpy::stdlib::termios::tcsetattr_raw")
def tcsetattr_raw(fd: int64, when: int32, iflag: int64, oflag: int64,
                  cflag: int64, lflag: int64, ispeed: int64, ospeed: int64,
                  cc: readonly[bytearray]) -> int32: ...
