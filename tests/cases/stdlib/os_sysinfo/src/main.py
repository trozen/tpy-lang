# os module constants (name/sep/.../devnull) + process identity (getpid/getuid
# family), umask, cpu_count, strerror, isatty, unlink. Identity values are
# machine-specific, so they're checked by property; constants and strerror(2)
# are deterministic (strerror uses the same libc as CPython). Byte-compared
# against CPython.
import os


def main():
    print(os.name, os.sep, os.pathsep, repr(os.linesep))
    print(os.curdir, os.pardir, os.extsep, os.devnull)

    print("pid", os.getpid() > 0, os.getppid() > 0)
    print("ids", os.getuid() >= 0, os.getgid() >= 0,
          os.geteuid() == os.getuid(), os.getegid() == os.getgid())

    c = os.cpu_count()
    print("cpu", c is not None and c > 0)

    print("strerror", os.strerror(2))          # No such file or directory
    print("isatty", os.isatty(99999))          # False (invalid fd)

    old = os.umask(0o27)
    restored = os.umask(old)
    print("umask", old >= 0, restored == 0o27)  # the second call returns 0o27

    # unlink is os.remove
    with open("/tmp/tpy_os_sysinfo_t", "w") as fh:
        fh.write("x")
    os.unlink("/tmp/tpy_os_sysinfo_t")
    print("unlink", not os.path.exists("/tmp/tpy_os_sysinfo_t"))


main()
