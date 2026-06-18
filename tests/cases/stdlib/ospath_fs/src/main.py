# os.path filesystem predicates + abspath, over fixed /tmp fixtures; chdir
# makes abspath deterministic. Symlink-True paths need os.symlink (not yet),
# so only the non-symlink behavior of islink/lexists is exercised.
import os
from os.path import exists, lexists, isfile, isdir, islink, getsize, abspath


def main():
    f = "/tmp/tpy_ospath_fs.txt"
    with open(f, "w") as fh:
        fh.write("abcde")

    print(exists(f), exists("/tmp/tpy_ospath_fs_missing"))
    print(isfile(f), isfile("/tmp"))
    print(isdir(f), isdir("/tmp"))
    print(islink(f), islink("/tmp"))
    print(lexists(f), lexists("/tmp/tpy_ospath_fs_missing"))
    print(getsize(f))
    # getsize on a directory returns st_size (does not raise, like CPython);
    # the value varies by filesystem, so only its non-negativity is checked.
    print(getsize("/tmp") >= 0)

    os.chdir("/tmp")
    print(abspath("sub/x"))
    print(abspath("/a/b/../c"))
    print(abspath("."))


main()
