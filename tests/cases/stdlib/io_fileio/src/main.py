# io.FileIO raw layer over a pipe fd: read(size) issues a single os.read,
# read(-1) drains to EOF, close()/closed, and closefd=False leaves the fd
# open for the caller. FileIO is @nocopy, so a silent copy across the
# FileIO(...) -> BufferedReader / local-binding boundary is a compile error.
import os
from io import FileIO
from tpy import int64


def feed(data: bytes) -> int64:
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)   # signal EOF on the read end
    return r


def main() -> None:
    f = FileIO(feed(b"hello world"))
    print(f.fileno() >= 0)
    print(f.read(5))      # b'hello'
    print(f.read(0))      # b'' (size 0 never touches the fd)
    print(f.read(-1))     # b' world'
    print(f.read(-1))     # b'' at EOF
    print(f.readable())
    print(f.closed)
    f.close()
    print(f.closed)

    # with-statement closes on exit.
    with FileIO(feed(b"ctx")) as cf:
        print(cf.read(-1))   # b'ctx'
    print(cf.closed)

    # A negative fd is rejected at construction (CPython parity).
    try:
        FileIO(int64(-1))
        print("no-raise")
    except ValueError:
        print("negfd-ValueError")

    # closefd=False: FileIO does not own the fd; the caller closes it. (If it
    # owned it, the os.close below would hit EBADF.)
    fd = feed(b"abc")
    g = FileIO(fd, closefd=False)
    print(g.read(-1))     # b'abc'
    g.close()
    os.close(fd)
    print("closefd-ok")

    # read(-1) over more than one 8192-byte raw read.
    payload = bytes(range(256)) * 80
    big = FileIO(feed(payload))
    got = big.read()
    print("read-all", len(got), got == payload)
    big.close()


main()
