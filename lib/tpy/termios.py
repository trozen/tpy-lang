# tpy: cpp_namespace("tpystd::termios")
"""POSIX terminal control, CPython-compatible surface.

`tcgetattr` returns a `TermAttributes` record instead of CPython's
seven-element list: it holds the same fields (iflag, oflag, cflag, lflag,
ispeed, ospeed, cc) and compares and prints exactly like that list, but it
is opaque -- no indexing, so a flag cannot be edited in place. The `tty`
helpers are the way to change a mode.

Every failure raises `termios.error` (an `Exception`, not an `OSError`, as in
CPython) whose `str()` is CPython's `(errno, 'strerror')`.
"""
from typing import Final

from tpy import int32, int64, Own
from tpy.extern import native_global
from _bindings import posix_termios
from os import strerror

# The names below are <termios.h> macros, so they bind to safe-named C globals
# (termios_impl.cpp) rather than being emitted as C++ constants the macros
# would rewrite. Flags are int64: tcflag_t is 64-bit on macOS.
TCSANOW: Final[int32] = native_global("tpy_const_termios_tcsanow", binding="C")
TCSADRAIN: Final[int32] = native_global("tpy_const_termios_tcsadrain", binding="C")
TCSAFLUSH: Final[int32] = native_global("tpy_const_termios_tcsaflush", binding="C")
VMIN: Final[int32] = native_global("tpy_const_termios_vmin", binding="C")
VTIME: Final[int32] = native_global("tpy_const_termios_vtime", binding="C")
NCCS: Final[int32] = native_global("tpy_const_termios_nccs", binding="C")

IGNBRK: Final[int64] = native_global("tpy_const_termios_ignbrk", binding="C")
BRKINT: Final[int64] = native_global("tpy_const_termios_brkint", binding="C")
IGNPAR: Final[int64] = native_global("tpy_const_termios_ignpar", binding="C")
PARMRK: Final[int64] = native_global("tpy_const_termios_parmrk", binding="C")
INPCK: Final[int64] = native_global("tpy_const_termios_inpck", binding="C")
ISTRIP: Final[int64] = native_global("tpy_const_termios_istrip", binding="C")
INLCR: Final[int64] = native_global("tpy_const_termios_inlcr", binding="C")
IGNCR: Final[int64] = native_global("tpy_const_termios_igncr", binding="C")
ICRNL: Final[int64] = native_global("tpy_const_termios_icrnl", binding="C")
IXON: Final[int64] = native_global("tpy_const_termios_ixon", binding="C")
IXANY: Final[int64] = native_global("tpy_const_termios_ixany", binding="C")
IXOFF: Final[int64] = native_global("tpy_const_termios_ixoff", binding="C")
OPOST: Final[int64] = native_global("tpy_const_termios_opost", binding="C")
PARENB: Final[int64] = native_global("tpy_const_termios_parenb", binding="C")
CSIZE: Final[int64] = native_global("tpy_const_termios_csize", binding="C")
CS8: Final[int64] = native_global("tpy_const_termios_cs8", binding="C")
ECHO: Final[int64] = native_global("tpy_const_termios_echo", binding="C")
ECHOE: Final[int64] = native_global("tpy_const_termios_echoe", binding="C")
ECHOK: Final[int64] = native_global("tpy_const_termios_echok", binding="C")
ECHONL: Final[int64] = native_global("tpy_const_termios_echonl", binding="C")
ICANON: Final[int64] = native_global("tpy_const_termios_icanon", binding="C")
IEXTEN: Final[int64] = native_global("tpy_const_termios_iexten", binding="C")
ISIG: Final[int64] = native_global("tpy_const_termios_isig", binding="C")
NOFLSH: Final[int64] = native_global("tpy_const_termios_noflsh", binding="C")
TOSTOP: Final[int64] = native_global("tpy_const_termios_tostop", binding="C")


class error(Exception):
    pass


def _message(err: int32) -> str:
    return "(" + str(err) + ", " + repr(strerror(err)) + ")"


class TermAttributes:
    _iflag: int64
    _oflag: int64
    _cflag: int64
    _lflag: int64
    _ispeed: int64
    _ospeed: int64
    _cc: bytearray

    def __init__(self, iflag: int64, oflag: int64, cflag: int64, lflag: int64,
                 ispeed: int64, ospeed: int64, cc: Own[bytearray]) -> None:
        self._iflag = iflag
        self._oflag = oflag
        self._cflag = cflag
        self._lflag = lflag
        self._ispeed = ispeed
        self._ospeed = ospeed
        self._cc = cc

    def __eq__(self, other: "TermAttributes") -> bool:
        return (self._iflag == other._iflag and self._oflag == other._oflag
                and self._cflag == other._cflag and self._lflag == other._lflag
                and self._ispeed == other._ispeed
                and self._ospeed == other._ospeed and self._cc == other._cc)

    # CPython's list repr. Its tcgetattr reports the VMIN and VTIME slots as
    # ints outside canonical mode (where they are EOF/EOL on some platforms)
    # and every other control character as a one-byte bytes.
    def __repr__(self) -> str:
        numeric = (self._lflag & ICANON) == 0
        out = ("[" + str(self._iflag) + ", " + str(self._oflag) + ", "
               + str(self._cflag) + ", " + str(self._lflag) + ", "
               + str(self._ispeed) + ", " + str(self._ospeed) + ", [")
        for i in range(len(self._cc)):
            if i > 0:
                out += ", "
            if numeric and (i == VMIN or i == VTIME):
                out += str(self._cc[i])
            else:
                out += repr(bytes([self._cc[i]]))
        return out + "]]"

    def __str__(self) -> str:
        return self.__repr__()

    def _copy(self) -> Own["TermAttributes"]:
        cc = bytearray(self._cc)
        return TermAttributes(self._iflag, self._oflag, self._cflag,
                              self._lflag, self._ispeed, self._ospeed, cc)

    # tty.cfmakeraw / tty.cfmakecbreak: CPython 3.12's masks.
    def _make_raw(self) -> None:
        self._iflag &= ~(IGNBRK | BRKINT | IGNPAR | PARMRK | INPCK | ISTRIP
                         | INLCR | IGNCR | ICRNL | IXON | IXANY | IXOFF)
        self._oflag &= ~OPOST
        self._cflag &= ~(PARENB | CSIZE)
        self._cflag |= CS8
        self._lflag &= ~(ECHO | ECHOE | ECHOK | ECHONL | ICANON | IEXTEN
                         | ISIG | NOFLSH | TOSTOP)
        self._read_one_byte()

    def _make_cbreak(self) -> None:
        self._lflag &= ~(ECHO | ICANON)
        self._read_one_byte()

    # Non-canonical read returns as soon as one byte arrives, with no timer.
    def _read_one_byte(self) -> None:
        self._cc[VMIN] = 1
        self._cc[VTIME] = 0


# CPython rejects a negative fd while converting the argument, before any
# syscall, so it is a ValueError rather than termios.error(EBADF).
def _check_fd(fd: int64) -> None:
    if fd < 0:
        raise ValueError("file descriptor cannot be a negative integer ("
                         + str(fd) + ")")


def tcgetattr(fd: int64) -> Own[TermAttributes]:
    _check_fd(fd)
    (err, iflag, oflag, cflag, lflag, ispeed, ospeed,
     cc) = posix_termios.tcgetattr_raw(fd)
    if err != 0:
        raise error(_message(err))
    return TermAttributes(iflag, oflag, cflag, lflag, ispeed, ospeed, cc)


def tcsetattr(fd: int64, when: int32, attributes: TermAttributes) -> None:
    _check_fd(fd)
    err = posix_termios.tcsetattr_raw(
        fd, when, attributes._iflag, attributes._oflag, attributes._cflag,
        attributes._lflag, attributes._ispeed, attributes._ospeed,
        attributes._cc)
    if err != 0:
        raise error(_message(err))
