# termios/tty on a pty: cbreak/raw modes, blocking reads and restoring saved
# attributes in each position, plus the attribute repr and the error paths.
import errno
import os
import termios
import tty

from tpy import int32, int64


# CPython's str() of termios.error: the (errno, strerror) tuple.
def expected(err: int32) -> str:
    return "(" + str(err) + ", " + repr(os.strerror(err)) + ")"


def free_function() -> None:
    master, slave = os.openpty()
    old = termios.tcgetattr(slave)
    # setcbreak hands back the mode in force before it.
    prev = tty.setcbreak(slave)  # tpyc: ok
    print("free: setcbreak returns old:", prev == old)
    # Already blocking: nothing to change.
    os.set_blocking(slave, True)  # tpyc: ok
    print("free: set_blocking no-op:", os.get_blocking(slave))
    # Reads that expect data block: pty input reaches the slave
    # asynchronously, so a non-blocking read could race the write. One byte,
    # so a VMIN=1 read cannot come back partial.
    os.write(master, b"a")
    print("free: cbreak read:", os.read(slave, 16))     # no newline needed
    os.set_blocking(slave, False)
    print("free: blocking:", os.get_blocking(slave))
    try:
        os.read(slave, 16)
    except BlockingIOError:
        print("free: cbreak empty: BlockingIOError")
    os.set_blocking(slave, True)
    tty.setraw(slave)
    os.write(master, b"\x03")
    print("free: raw read:", os.read(slave, 16))        # Ctrl-C is a byte, no SIGINT
    # Hands the saved attributes back. Every restore that is compared uses
    # TCSAFLUSH: on macOS re-entering canonical mode with TCSANOW/TCSADRAIN
    # sets PENDIN in lflag, so the read-back would differ (CPython too).
    termios.tcsetattr(slave, termios.TCSAFLUSH, old)  # tpyc: ok
    print("free: restored:", termios.tcgetattr(slave) == old)
    # != compares by value, like CPython's list.
    print("free: restored differs:", termios.tcgetattr(slave) != old)  # tpyc: ok
    os.write(master, b"cd")
    os.set_blocking(slave, False)
    try:
        os.read(slave, 16)
    except BlockingIOError:
        print("free: canonical waits for newline")
    os.set_blocking(slave, True)
    print("free: blocking again:", os.get_blocking(slave))
    os.write(master, b"\n")
    # A blocking canonical read returns the whole line.
    print("free: canonical read:", os.read(slave, 16))

    # cfmakecbreak mutates the one object both names refer to.
    attrs = termios.tcgetattr(slave)
    alias = attrs
    tty.cfmakecbreak(alias)
    print("free: alias mutated original:", attrs != old)
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    print("free: applied:", termios.tcgetattr(slave) == attrs)
    termios.tcsetattr(slave, termios.TCSANOW, old)

    r, w = os.pipe()
    try:
        termios.tcgetattr(r)
    except OSError:
        print("free: wrong: termios.error is not an OSError")
    except termios.error as e:
        print("free: not a tty: termios.error", str(e) == expected(errno.ENOTTY))
    for fd in [master, slave, r, w]:
        os.close(fd)


class RawSession:
    saved: "termios.TermAttributes"

    def __init__(self, fd: int64) -> None:
        self.fd = fd
        # The previous mode lives in a field until restore() runs.
        self.saved = tty.setraw(fd)  # tpyc: ok

    def restore(self) -> None:
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, self.saved)


def method() -> None:
    master, slave = os.openpty()
    before = termios.tcgetattr(slave)
    session = RawSession(slave)
    print("method: raw differs:", termios.tcgetattr(slave) != before)
    session.restore()
    print("method: restored:", termios.tcgetattr(slave) == before)
    os.close(master)
    os.close(slave)


def try_finally() -> None:
    master, slave = os.openpty()
    old = termios.tcgetattr(slave)
    try:
        # TCSADRAIN only where no echo is queued: on macOS it waits for the
        # pty's unread output to drain, which here nothing ever reads.
        tty.setcbreak(slave, termios.TCSADRAIN)  # tpyc: ok
        os.write(master, b"x")
        print("finally: read:", os.read(slave, 1))
    finally:
        # Restores even if the body raised.
        termios.tcsetattr(slave, termios.TCSAFLUSH, old)  # tpyc: ok
    print("finally: restored:", termios.tcgetattr(slave) == old)
    os.close(master)
    os.close(slave)


class CbreakMode:
    saved: "termios.TermAttributes"

    def __init__(self, fd: int64) -> None:
        self.fd = fd
        self.saved = termios.tcgetattr(fd)

    def __enter__(self) -> "CbreakMode":
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Restores when the with block ends.
        termios.tcsetattr(self.fd, termios.TCSAFLUSH, self.saved)  # tpyc: ok


def context_manager() -> None:
    master, slave = os.openpty()
    old = termios.tcgetattr(slave)
    with CbreakMode(slave):
        os.write(master, b"y")
        print("with: read:", os.read(slave, 1))
        print("with: cbreak:", termios.tcgetattr(slave) != old)
    print("with: restored:", termios.tcgetattr(slave) == old)
    os.close(master)
    os.close(slave)


# Flag values and cc positions differ between OSes, so only structural facts
# about the repr are printed.
def repr_str() -> None:
    master, slave = os.openpty()
    attrs = termios.tcgetattr(slave)
    text = repr(attrs)  # tpyc: ok
    print("repr: repr equals str:", text == str(attrs))
    print("repr: ends with ]]:", text.endswith("]]"))
    cooked = text[text.index(", [") + 3:-2].split(", ")
    print("repr: cooked cc all bytes:", len(cooked) == termios.NCCS
          and all(item.startswith("b") for item in cooked))
    old = tty.setraw(slave)
    raw = repr(termios.tcgetattr(slave))
    # Outside canonical mode VMIN and VTIME print as ints, the rest as bytes.
    items = raw[raw.index(", [") + 3:-2].split(", ")
    nbytes = 0
    for item in items:
        if item.startswith("b"):
            nbytes += 1
    print("repr: raw cc bytes:", nbytes == termios.NCCS - 2,
          "ints:", len(items) - nbytes)
    termios.tcsetattr(slave, termios.TCSANOW, old)
    os.close(master)
    os.close(slave)


def tcsetattr_errors() -> None:
    master, slave = os.openpty()
    attrs = termios.tcgetattr(slave)
    try:
        # 99 names no TCSA* action.
        termios.tcsetattr(slave, 99, attrs)  # tpyc: ok
    except termios.error as e:
        print("seterr: bad action: EINVAL", str(e) == expected(errno.EINVAL))
    os.close(master)
    os.close(slave)
    try:
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
    except termios.error as e:
        print("seterr: closed fd: EBADF", str(e) == expected(errno.EBADF))
    try:
        os.get_blocking(slave)  # tpyc: ok
    except OSError:
        print("seterr: closed get_blocking: OSError")
    try:
        os.set_blocking(slave, True)  # tpyc: ok
    except OSError:
        print("seterr: closed set_blocking: OSError")


def icrnl() -> None:
    master, slave = os.openpty()
    old = tty.setcbreak(slave)
    # cbreak keeps ICRNL, so the terminal turns a CR into a NL (CPython
    # 3.12.2 and later; 3.12.0 and 3.12.1 cleared it).
    os.write(master, b"\r")
    print("icrnl: cbreak:", os.read(slave, 16))
    # raw clears ICRNL, so the CR arrives as is.
    tty.setraw(slave)
    os.write(master, b"\r")
    print("icrnl: raw:", os.read(slave, 16))
    termios.tcsetattr(slave, termios.TCSANOW, old)
    os.close(master)
    os.close(slave)


def negative_fd() -> None:
    master, slave = os.openpty()
    attrs = termios.tcgetattr(slave)
    # A negative fd is a ValueError before any syscall, not termios.error.
    try:
        termios.tcgetattr(-1)  # tpyc: ok
    except ValueError as e:
        print("negfd: tcgetattr: ValueError", str(e))
    try:
        termios.tcsetattr(-5, termios.TCSANOW, attrs)
    except ValueError as e:
        print("negfd: tcsetattr: ValueError", str(e))
    try:
        tty.setraw(-2)
    except ValueError as e:
        print("negfd: setraw: ValueError", str(e))
    os.close(master)
    os.close(slave)


def main() -> None:
    free_function()
    method()
    try_finally()
    context_manager()
    repr_str()
    tcsetattr_errors()
    icrnl()
    negative_fd()


main()
