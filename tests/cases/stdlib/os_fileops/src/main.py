# os.link (hardlink), truncate, ftruncate, fsync. Builds + tears down
# a /tmp file so both phases start clean. Byte-compared against CPython.
import os


def main():
    p = "/tmp/tpy_os_fileops"
    ln = "/tmp/tpy_os_fileops_ln"
    if os.path.exists(ln):
        os.remove(ln)
    if os.path.exists(p):
        os.remove(p)

    with open(p, "w") as fh:
        fh.write("hello world")
    os.truncate(p, 5)
    print("truncate", os.stat(p).st_size)            # 5

    fd = os.open(p, os.O_WRONLY)
    os.ftruncate(fd, 2)
    os.fsync(fd)
    os.close(fd)
    print("ftruncate", os.stat(p).st_size)           # 2

    os.link(p, ln)                                   # hardlink
    print("link", os.path.exists(ln), os.stat(ln).st_size)   # True 2
    os.remove(ln)
    os.remove(p)


main()
