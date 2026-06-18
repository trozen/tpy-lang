# os.stat/lstat + stat_result + os.path stat queries (getmtime/samefile/
# ismount) + mkdir(mode=). Stat values are machine-dependent, so this asserts
# only invariants (size of known content, S_IF* type bits, cross-consistency),
# never raw ino/dev/uid/time values. Byte-compared against CPython.
import os
from os.path import getmtime, getatime, getctime, getsize, samefile, ismount


def teardown(base: str) -> None:
    if os.path.lexists(base + "/lnk"):
        os.remove(base + "/lnk")
    if os.path.exists(base + "/f.txt"):
        os.remove(base + "/f.txt")
    if os.path.exists(base + "/d700"):
        os.rmdir(base + "/d700")
    if os.path.exists(base):
        os.rmdir(base)


def main():
    base = "/tmp/tpy_os_stat"
    teardown(base)
    os.mkdir(base)
    with open(base + "/f.txt", "w") as fh:
        fh.write("hello")

    st = os.stat(base + "/f.txt")
    print("size:", st.st_size)                                # 5 (deterministic)
    print("is-reg:", st.st_mode & 0o170000 == 0o100000)       # S_IFREG
    print("nlink>=1:", st.st_nlink >= 1)
    # both timestamp forms are populated and positive for a just-created file
    print("mtime>0:", st.st_mtime > 0.0, st.st_mtime_ns > 0)
    # os.path queries cross-check against the record
    print("getsize:", getsize(base + "/f.txt") == st.st_size)
    print("getmtime:", getmtime(base + "/f.txt") == st.st_mtime)
    print("getatime:", getatime(base + "/f.txt") == st.st_atime)
    print("getctime:", getctime(base + "/f.txt") == st.st_ctime)

    print("dir-is-dir:", os.stat(base).st_mode & 0o170000 == 0o040000)

    os.symlink(base + "/f.txt", base + "/lnk")
    print("lstat-is-link:", os.lstat(base + "/lnk").st_mode & 0o170000 == 0o120000)
    print("stat-follows-link:", os.stat(base + "/lnk").st_size == 5)
    print("samefile-self:", samefile(base + "/f.txt", base + "/f.txt"))
    print("samefile-via-link:", samefile(base + "/lnk", base + "/f.txt"))

    print("ismount-base:", ismount(base))     # False
    print("ismount-root:", ismount("/"))       # True

    # mkdir(mode): group/other bits are never set for 0o700, umask-independent.
    os.mkdir(base + "/d700", 0o700)
    print("mkdir-mode:", os.stat(base + "/d700").st_mode & 0o077 == 0)

    teardown(base)
    print("clean:", os.path.exists(base))


main()
