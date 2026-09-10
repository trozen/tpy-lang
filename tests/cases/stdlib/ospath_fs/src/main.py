# os.path filesystem predicates + abspath, over fixtures in the run's scratch
# cwd. Paths are built from getcwd() (already canonical, no symlink component)
# so the predicates and abspath stay host-independent: islink(dir) is False,
# and cwd-relative abspath is compared against the dir rather than printed.
import os
from os.path import exists, lexists, isfile, isdir, islink, getsize, abspath


def main():
    tmp = os.getcwd()
    f = tmp + "/tpy_ospath_fs.txt"
    with open(f, "w") as fh:
        fh.write("abcde")

    print(exists(f), exists(tmp + "/tpy_ospath_fs_missing"))
    print(isfile(f), isfile(tmp))
    print(isdir(f), isdir(tmp))
    print(islink(f), islink(tmp))
    print(lexists(f), lexists(tmp + "/tpy_ospath_fs_missing"))
    print(getsize(f))
    # getsize on a directory returns st_size (does not raise, like CPython);
    # the value varies by filesystem, so only its non-negativity is checked.
    print(getsize(tmp) >= 0)

    os.chdir(tmp)
    print(abspath("sub/x") == tmp + "/sub/x")
    print(abspath("/a/b/../c"))
    print(abspath(".") == tmp)


main()
