# io.FileIO raw layer and io.BufferedWriter over it: reads, writes, mode
# validation, append seeks, closed-object checks, close/finalizer behavior.
# FileIO is @nocopy, so a silent copy at a FileIO(...) boundary is an error.
import os
from io import BufferedReader, BufferedWriter, BytesIO, FileIO, StringIO
from tpy import int64


def feed(data: bytes) -> int64:
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)   # signal EOF on the read end
    return r


def basics() -> None:
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


def modes() -> None:
    r, w = os.pipe()
    # Scanned like CPython: repeated 'b' is fine, a second r/w/x/a or '+' is
    # the "exactly one" error, any other character "invalid mode" -- whichever
    # comes first.
    for m in ["rbb", "bw", "r+", "x", "+", "rr", "r++", "wx", "", "b", "rq",
              "qrr", "rrq"]:
        try:
            f = FileIO(r, m, False)
            print("mode:", m, f.readable(), f.writable())
        except ValueError as e:
            print("mode:", m, str(e))
    # The fd is checked before the mode.
    try:
        FileIO(int64(-1), "q")
        print("mode: accepted")
    except ValueError as e:
        print("mode:", str(e))
    # "a" on a pipe: the seek to the end fails with ESPIPE, which is ignored.
    a = FileIO(w, "a", False)
    print("mode: a", a.writable(), a.write(b"p"), os.read(r, 4))
    os.close(r)
    os.close(w)


def append() -> None:
    fd = os.open("fileio_append.txt", os.O_RDWR | os.O_CREAT | os.O_TRUNC)
    os.write(fd, b"hello")
    os.lseek(fd, 0, os.SEEK_SET)
    # "a" seeks the adopted fd to the end, so the write does not overwrite.
    f = FileIO(fd, "a", False)
    print("append:", os.lseek(fd, 0, os.SEEK_CUR))
    f.write(b"XY")
    f.close()
    os.lseek(fd, 0, os.SEEK_SET)
    print("append:", os.read(fd, 16))
    os.close(fd)
    os.unlink("fileio_append.txt")


def access() -> None:
    r, w = os.pipe()
    fw = FileIO(w, "w")
    print("access:", fw.writable(), fw.readable(), fw.write(b"zz"))
    # CPython raises io.UnsupportedOperation, an OSError subclass.
    try:
        fw.read(1)
        print("access: read")
    except OSError as e:
        print("access:", str(e))
    try:
        fw.read()
        print("access: read")
    except OSError as e:
        print("access:", str(e))
    fw.close()
    fr = FileIO(r, "rb")
    print("access:", fr.readable(), fr.writable(), fr.read(8))
    try:
        fr.write(b"no")
        print("access: wrote")
    except OSError as e:
        print("access:", str(e))
    fr.close()


def closed_checks() -> None:
    # readable()/writable() of a closed object raise, as in CPython; BytesIO's
    # message is the one with a trailing period. The result is bound before
    # the print, which would otherwise emit its label first
    # (BUGS.md#print-arg-output-interleaves).
    r, w = os.pipe()
    f = FileIO(r, "r", False)
    f.close()
    try:
        ok = f.readable()
        print("closed: FileIO readable", ok)
    except ValueError as e:
        print("closed: FileIO readable", str(e))
    try:
        ok = f.writable()
        print("closed: FileIO writable", ok)
    except ValueError as e:
        print("closed: FileIO writable", str(e))
    br = BufferedReader(FileIO(r, "r", False))
    br.close()
    try:
        ok = br.readable()
        print("closed: BufferedReader readable", ok)
    except ValueError as e:
        print("closed: BufferedReader readable", str(e))
    bw = BufferedWriter(FileIO(w, "w", False))
    bw.close()
    try:
        ok = bw.writable()
        print("closed: BufferedWriter writable", ok)
    except ValueError as e:
        print("closed: BufferedWriter writable", str(e))
    os.close(r)
    os.close(w)
    s = StringIO()
    s.close()
    try:
        ok = s.readable()
        print("closed: StringIO readable", ok)
    except ValueError as e:
        print("closed: StringIO readable", str(e))
    try:
        ok = s.writable()
        print("closed: StringIO writable", ok)
    except ValueError as e:
        print("closed: StringIO writable", str(e))
    try:
        ok = s.seekable()
        print("closed: StringIO seekable", ok)
    except ValueError as e:
        print("closed: StringIO seekable", str(e))
    b = BytesIO()
    b.close()
    try:
        ok = b.readable()
        print("closed: BytesIO readable", ok)
    except ValueError as e:
        print("closed: BytesIO readable", str(e))
    try:
        ok = b.writable()
        print("closed: BytesIO writable", ok)
    except ValueError as e:
        print("closed: BytesIO writable", str(e))
    try:
        ok = b.seekable()
        print("closed: BytesIO seekable", ok)
    except ValueError as e:
        print("closed: BytesIO seekable", str(e))


def failing_close() -> None:
    r, w = os.pipe()
    bw = BufferedWriter(FileIO(w, "wb"))
    os.close(w)
    # The close error surfaces once; the FileIO gave the fd up before
    # os.close, so its finalizer does not close it again.
    try:
        bw.close()
        print("failclose: closed")
    except OSError as e:
        print("failclose:", e.errno)
    print("failclose:", bw.closed)
    os.close(r)


def buffered_writer() -> None:
    r, w = os.pipe()
    os.set_blocking(r, False)
    bw = BufferedWriter(FileIO(w, "wb"))
    print("bufw:", bw.write(b"abc"), bw.writable(), bw.fileno() == w)
    # Nothing reaches the pipe before the flush.
    try:
        os.read(r, 16)
        print("bufw: data before flush")
    except BlockingIOError:
        print("bufw: empty before flush")
    bw.flush()
    print("bufw:", os.read(r, 16))
    # A write larger than the buffer goes to the raw sink at once.
    print("bufw:", bw.write(b"z" * 9000), len(os.read(r, 10000)))
    bw.close()
    print("bufw:", bw.closed)
    try:
        bw.write(b"late")
        print("bufw: wrote after close")
    except ValueError as e:
        print("bufw:", str(e))
    try:
        bw.flush()
        print("bufw: flushed after close")
    except ValueError as e:
        print("bufw:", str(e))
    os.close(r)


def small_buffer() -> None:
    r, w = os.pipe()
    os.set_blocking(r, False)
    bw = BufferedWriter(FileIO(w, "wb"), 8)
    bw.write(b"abcde")
    # Overflows the 5 pending bytes: they are flushed, the new 5 fit and stay
    # buffered.
    bw.write(b"fghij")
    print("smallbuf:", os.read(r, 16))
    bw.flush()
    print("smallbuf:", os.read(r, 16))
    bw.close()
    os.close(r)
    for size in [0, -1]:
        r2, w2 = os.pipe()
        try:
            BufferedWriter(FileIO(w2, "wb"), size)
            print("smallbuf: accepted")
        except ValueError as e:
            print("smallbuf:", size, str(e))
        os.close(r2)


def drop_unclosed(w: int64) -> None:
    bw = BufferedWriter(FileIO(w, "wb"))
    bw.write(b"tail")


def finalizer_flush() -> None:
    r, w = os.pipe()
    # Dropped without close(): the finalizer flushes and closes the fd, so
    # the reader sees the bytes and then EOF.
    drop_unclosed(w)
    print("final:", os.read(r, 16), os.read(r, 16))
    os.close(r)


def main() -> None:
    basics()
    modes()
    append()
    access()
    closed_checks()
    failing_close()
    buffered_writer()
    small_buffer()
    finalizer_flush()


main()
