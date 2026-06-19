# os.path.relpath + realpath. relpath output is machine-independent (both sides
# made absolute, so the cwd cancels in the common prefix). realpath is checked
# on non-existent ABSOLUTE paths (pure lexical -- no symlink, no cwd) plus
# cwd-relative property checks; this avoids /tmp, which is a symlink on macOS.
# Byte-compared against CPython.
import os
from os.path import realpath, relpath


def main():
    print(relpath("/a/b/c", "/a/b"))      # c
    print(relpath("/a/b/c", "/a/x/y"))    # ../../b/c
    print(relpath("/a/b", "/a/b"))        # .
    print(relpath("a/b", "a"))            # b   (cwd cancels)
    print(relpath("/x", "/"))             # x
    print(relpath("/a/b/c/d", "/a"))      # b/c/d

    # realpath on non-existent absolute paths: lexical normalize, no error.
    print(realpath("/nope_xyz/a/../b"))   # /nope_xyz/b
    print(realpath("/x_zzz/./y/../z"))    # /x_zzz/z
    print(realpath("/"))                  # /
    print(realpath("//"))                 # /

    # cwd-relative properties (machine-independent booleans).
    print(realpath("") == realpath(os.getcwd()))    # True ("" is cwd)
    print(realpath(".") == realpath(os.getcwd()))   # True
    print(realpath("rel_zzz").endswith("/rel_zzz"))  # True (absolutized)


main()
