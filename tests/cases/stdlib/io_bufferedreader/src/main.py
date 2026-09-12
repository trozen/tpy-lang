# io.BufferedReader over a pipe-backed FileIO: header-style readline loop,
# read(n)/read(-1)/read(0), partial last line at EOF, multi-fill (tiny
# buffer), readline(size) cap, iteration, readlines, and read-after-close
# raising ValueError. @nocopy makes a silent copy across the Own move a
# compile error (covers the value-vs-reference boundary).
import os
from io import FileIO, BufferedReader
from tpy import int64


def feed(data: bytes) -> int64:
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)
    return r


def main() -> None:
    # Header lines via readline, then body via read().
    br = BufferedReader(FileIO(
        feed(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello")))
    print(br.readline())
    print(br.readline())
    print(br.readline())
    print(br.read())          # b'hello'
    print(br.read())          # b'' at EOF
    print(br.closed)
    br.close()
    print(br.closed)

    # read(n): exact, read(0), then more-than-remaining.
    r2 = BufferedReader(FileIO(feed(b"abcdefgh")))
    print(r2.read(3))         # b'abc'
    print(r2.read(0))         # b''
    print(r2.read(100))       # b'defgh'

    # Partial last line (no trailing newline) returned without raising.
    r3 = BufferedReader(FileIO(feed(b"one\ntwo")))
    print(r3.readline())      # b'one\n'
    print(r3.readline())      # b'two'
    print(r3.readline())      # b''

    # Multi-fill: a 3-byte buffer forces several refills to assemble one line.
    r4 = BufferedReader(FileIO(feed(b"abcdefghij\n")), buffer_size=3)
    print(r4.readline())      # b'abcdefghij\n'

    # readline(size) caps the returned length.
    r5 = BufferedReader(FileIO(feed(b"abcdef\n")))
    print(r5.readline(3))     # b'abc'
    print(r5.read())          # b'def\n'

    # Iteration yields lines; readlines collects them.
    r6 = BufferedReader(FileIO(feed(b"x\ny\nz\n")))
    for line in r6:
        print(line)
    r7 = BufferedReader(FileIO(feed(b"p\nq\n")))
    lines = r7.readlines()
    print(len(lines))
    for ln in lines:
        print(ln)

    # read(size) blocks until `size` or EOF: a 3-byte buffer needs several
    # fills to satisfy read(7) (exercises the read-side refill loop).
    r8 = BufferedReader(FileIO(feed(b"abcdefghij")), buffer_size=3)
    print(r8.read(7))         # b'abcdefg'
    print(r8.read())          # b'hij'

    # Empty source: EOF on the first fill, no looping.
    r9 = BufferedReader(FileIO(feed(b"")))
    print(r9.read())          # b''
    print(r9.readline())      # b''

    # with-statement closes on exit.
    with BufferedReader(FileIO(feed(b"z\n"))) as bc:
        print(bc.readline())  # b'z\n'
    print(bc.closed)

    # buffer_size <= 0 is rejected (CPython parity).
    try:
        BufferedReader(FileIO(feed(b"x")), buffer_size=0)
        print("no-raise")
    except ValueError:
        print("bufsize-ValueError")

    # read after close raises ValueError (CPython parity).
    rc = BufferedReader(FileIO(feed(b"data")))
    rc.close()
    try:
        rc.read()
        print("no-raise")
    except ValueError:
        print("closed-ValueError")


main()
