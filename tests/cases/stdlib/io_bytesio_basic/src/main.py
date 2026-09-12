# io.BytesIO -- write/read/seek/iter + context manager + closed errors.
import io
from tpy import int32


def basic_write_read() -> None:
    b = io.BytesIO()
    n1 = b.write(b"hello ")
    n2 = b.write(b"world")
    print("wrote:", n1 + n2)
    print("getvalue:", b.getvalue())
    print("tell:", b.tell())


def initial_value_and_overwrite() -> None:
    b = io.BytesIO(b"hello")
    print("initial pos:", b.tell())
    b.write(b"HE")
    print("after-overwrite:", b.getvalue())
    b.write(b"LLO WORLD")
    print("after-extend:", b.getvalue())


def seek_then_read() -> None:
    b = io.BytesIO(b"abcdefgh")
    b.seek(int32(3))
    print("read-from-3:", b.read())
    b.seek(int32(0))
    print("read-all:", b.read())


def readline_iteration() -> None:
    b = io.BytesIO(b"line1\nline2\nline3")
    print("readline-1:", b.readline())
    print("readline-2:", b.readline())
    print("readline-3:", b.readline())
    print("readline-4-eof-len:", len(b.readline()))


def context_manager_and_close() -> None:
    with io.BytesIO(b"ctx") as b:
        print("inside:", b.read())
    b2 = io.BytesIO(b"x")
    b2.close()
    print("closed:", b2.closed)
    try:
        b2.write(b"y")
        print("FAIL")
    except ValueError as e:
        print("got ValueError on write:", str(e))


def main() -> None:
    basic_write_read()
    print("---")
    initial_value_and_overwrite()
    print("---")
    seek_then_read()
    print("---")
    readline_iteration()
    print("---")
    context_manager_and_close()


main()
