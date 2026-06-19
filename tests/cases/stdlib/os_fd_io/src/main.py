# Low-level fd I/O: open/write/close, open/lseek (all three whence)/read/fstat,
# pipe, dup, dup2. Uses the real O_*/SEEK_* constants. Builds + tears down a /tmp
# file so both phases start clean. Byte-compared against CPython.
import os


def main():
    p = "/tmp/tpy_os_fd_io"
    if os.path.exists(p):
        os.remove(p)

    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    print("wrote", os.write(fd, b"hello world"))
    os.close(fd)

    fd = os.open(p, os.O_RDONLY)
    print("head", os.read(fd, 5))                 # b'hello'
    os.lseek(fd, 6, os.SEEK_SET)
    print("seek-read", os.read(fd, 5))            # b'world'
    os.lseek(fd, -5, os.SEEK_END)
    print("end-read", os.read(fd, 5))             # b'world'
    os.lseek(fd, 0, os.SEEK_SET)
    os.lseek(fd, 6, os.SEEK_CUR)                   # relative seek
    print("cur-read", os.read(fd, 5))             # b'world'
    print("fstat", os.fstat(fd).st_size)          # 11
    os.close(fd)

    # pipe: write end -> read end
    r, w = os.pipe()
    os.write(w, b"pipe!")
    print("pipe", os.read(r, 5))                   # b'pipe!'
    os.close(r)
    os.close(w)

    # dup / dup2: duplicate fd refers to the same open file
    fd = os.open(p, os.O_RDONLY)
    d = os.dup(fd)
    print("dup-read", os.read(d, 5))               # b'hello'
    os.close(d)
    fd2 = os.open(os.devnull, os.O_RDONLY)
    os.dup2(fd, fd2)                               # fd2 now refers to p
    print("dup2-read", os.read(fd2, 5))            # b'hello'
    os.close(fd2)
    os.close(fd)

    os.remove(p)


main()
