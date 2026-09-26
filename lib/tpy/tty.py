# tpy: cpp_namespace("tpystd::tty")
"""Terminal mode helpers over `termios`, CPython 3.12 surface.

`setraw` / `setcbreak` return the attributes in force before the change, so
the caller can hand them back to `termios.tcsetattr` to restore the terminal.
Unlike CPython, the termios names are not re-exported here.
"""
from tpy import int32, int64, Own, dispatch
import termios
from termios import TermAttributes


def cfmakeraw(mode: TermAttributes) -> None:
    mode._make_raw()


def cfmakecbreak(mode: TermAttributes) -> None:
    mode._make_cbreak()


# CPython's `when=TCSAFLUSH` default is spelled as an overload because a
# native_global constant cannot be a parameter default yet
# (BUGS.md#imported-final-param-default).
@dispatch
def setraw(fd: int64, when: int32) -> Own[TermAttributes]:
    mode = termios.tcgetattr(fd)
    new = mode._copy()
    cfmakeraw(new)
    termios.tcsetattr(fd, when, new)
    return mode


@dispatch
def setraw(fd: int64) -> Own[TermAttributes]:
    return setraw(fd, termios.TCSAFLUSH)


@dispatch
def setcbreak(fd: int64, when: int32) -> Own[TermAttributes]:
    mode = termios.tcgetattr(fd)
    new = mode._copy()
    cfmakecbreak(new)
    termios.tcsetattr(fd, when, new)
    return mode


@dispatch
def setcbreak(fd: int64) -> Own[TermAttributes]:
    return setcbreak(fd, termios.TCSAFLUSH)
