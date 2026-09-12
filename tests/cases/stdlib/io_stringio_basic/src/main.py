# io.StringIO -- write/read/seek/iter + context manager + closed errors.
import io
from tpy import int32


def basic_write_read() -> None:
    s = io.StringIO()
    n1 = s.write("hello ")
    n2 = s.write("world")
    print("wrote:", n1 + n2)
    print("getvalue:", s.getvalue())
    print("tell:", s.tell())


def initial_value_and_overwrite() -> None:
    s = io.StringIO("hello")
    print("initial pos:", s.tell())  # 0 per CPython
    print("initial getvalue:", s.getvalue())
    s.write("HE")  # overwrites first 2 chars
    print("after-overwrite getvalue:", s.getvalue())
    print("after-overwrite pos:", s.tell())
    s.write("LLO WORLD")  # extends past original end
    print("after-extend getvalue:", s.getvalue())


def seek_then_read() -> None:
    s = io.StringIO("abcdefgh")
    s.seek(int32(3))
    print("read-from-3:", s.read())
    s.seek(int32(0))
    print("read-all:", s.read())


def readline_iteration() -> None:
    s = io.StringIO("line1\nline2\nline3-no-newline")
    print("readline-1:", s.readline())
    print("readline-2:", s.readline())
    print("readline-3:", s.readline())
    print("readline-4 (eof):", s.readline())


def for_iter() -> None:
    s = io.StringIO("a\nb\nc\n")
    for line in s:
        print("iter-line:", line)


def context_manager() -> None:
    with io.StringIO("ctxmgr") as s:
        print("inside-ctxmgr:", s.read())
    # After the block, s is closed.


def closed_raises() -> None:
    s = io.StringIO("data")
    s.close()
    print("closed:", s.closed)
    try:
        s.read()
        print("FAIL: read should raise")
    except ValueError as e:
        print("got ValueError on read:", str(e))


def truncate_basic() -> None:
    s = io.StringIO("abcdefgh")
    s.seek(int32(3))
    s.truncate()  # truncate to current position (3)
    print("truncate-default:", s.getvalue())
    s.seek(int32(0))
    s.truncate(int32(2))
    print("truncate-explicit:", s.getvalue())


def main() -> None:
    basic_write_read()
    print("---")
    initial_value_and_overwrite()
    print("---")
    seek_then_read()
    print("---")
    readline_iteration()
    print("---")
    for_iter()
    print("---")
    context_manager()
    print("---")
    closed_raises()
    print("---")
    truncate_basic()


main()
