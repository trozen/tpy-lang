# os mutating ops: mkdir/makedirs/listdir(controlled dir)/symlink/rename/
# replace/remove/rmdir. Builds + tears down a fresh /tmp tree so both phases
# start clean; byte-compared against CPython.
import os
from os.path import isdir, isfile, islink, lexists, exists


def teardown(base: str) -> None:
    if lexists(base + "/lnk"):
        os.remove(base + "/lnk")
    for n in ["one.txt", "two.txt", "renamed.txt", "final.txt"]:
        if exists(base + "/" + n):
            os.remove(base + "/" + n)
    if exists(base + "/a/b"):
        os.rmdir(base + "/a/b")
    if exists(base + "/a"):
        os.rmdir(base + "/a")
    if exists(base):
        os.rmdir(base)


def main():
    base = "/tmp/tpy_os_mutate"
    teardown(base)

    os.mkdir(base)
    os.makedirs(base + "/a/b")
    os.makedirs(base + "/a/b", exist_ok=True)   # already exists -> no error
    print("makedirs:", isdir(base + "/a"), isdir(base + "/a/b"))

    for n in ["one.txt", "two.txt"]:
        with open(base + "/" + n, "w") as fh:
            fh.write(n)
    # controlled dir -> the full listing is deterministic ("a" + the two files)
    print("listdir:", ",".join(sorted(os.listdir(base))))

    os.symlink(base + "/one.txt", base + "/lnk")
    print("symlink:", islink(base + "/lnk"), lexists(base + "/lnk"),
          isfile(base + "/lnk"))   # isfile follows the link -> True
    print("readlink:", os.readlink(base + "/lnk"))

    os.rename(base + "/one.txt", base + "/renamed.txt")
    print("rename:", exists(base + "/renamed.txt"), exists(base + "/one.txt"))
    os.replace(base + "/two.txt", base + "/final.txt")
    print("replace:", exists(base + "/final.txt"), exists(base + "/two.txt"))

    os.remove(base + "/lnk")
    os.remove(base + "/renamed.txt")
    os.remove(base + "/final.txt")
    # removedirs prunes b, a, then base (all empty after the files are gone).
    os.removedirs(base + "/a/b")
    print("torn down:", exists(base))


main()
