"""Terminal mode helpers over `termios`, CPython 3.12 surface.

`setraw` / `setcbreak` return the attributes in force before the change, so
the caller can hand them back to `termios.tcsetattr` to restore the terminal.
Unlike CPython, the termios names are not re-exported here.
"""
__tpy__ = {"cpp_namespace": "tpystd::tty"}
from tpy import int32, int64, Own
import termios
from termios import TermAttributes


def cfmakeraw(mode: TermAttributes) -> None:
    mode._make_raw()


def cfmakecbreak(mode: TermAttributes) -> None:
    mode._make_cbreak()


def setraw(fd: int64, when: int32 = termios.TCSAFLUSH) -> Own[TermAttributes]:
    mode = termios.tcgetattr(fd)
    new = mode._copy()
    cfmakeraw(new)
    termios.tcsetattr(fd, when, new)
    return mode


def setcbreak(fd: int64,
              when: int32 = termios.TCSAFLUSH) -> Own[TermAttributes]:
    mode = termios.tcgetattr(fd)
    new = mode._copy()
    cfmakecbreak(new)
    termios.tcsetattr(fd, when, new)
    return mode
