# os mutating-op error mapping: each errno maps to its CPython OSError subclass
# (FileExists/NotADirectory/FileNotFound/non-empty, plus the host-divergent
# unlink-on-a-dir). Only the exception TYPE is observed; messages differ.
import os
from os.path import exists, lexists


def teardown(base: str) -> None:
    if exists(base + "/f.txt"):
        os.remove(base + "/f.txt")
    if exists(base + "/sub"):
        os.rmdir(base + "/sub")
    if exists(base):
        os.rmdir(base)


def main():
    base = "/tmp/tpy_os_mutate_err"
    teardown(base)
    os.mkdir(base)
    os.mkdir(base + "/sub")
    with open(base + "/f.txt", "w") as fh:
        fh.write("x")

    try:
        os.mkdir(base)                 # EEXIST
    except FileExistsError:
        print("mkdir-existing: FileExistsError")
    try:
        os.makedirs(base + "/sub")     # exist_ok defaults False
    except FileExistsError:
        print("makedirs-existing: FileExistsError")
    try:
        os.rmdir(base + "/f.txt")      # ENOTDIR (rmdir on a file)
    except NotADirectoryError:
        print("rmdir-file: NotADirectoryError")
    try:
        os.remove(base + "/sub")       # unlink on a dir: EISDIR (Linux) / EPERM (macOS)
    # Per-host errno -> different class; normalize to one token.
    except (IsADirectoryError, PermissionError):
        print("remove-dir: rejected")
    try:
        os.rmdir(base)                 # ENOTEMPTY -> plain OSError (no subclass)
    except OSError:
        print("rmdir-nonempty: OSError")
    try:
        os.rmdir(base + "/missing")    # ENOENT
    except FileNotFoundError:
        print("rmdir-missing: FileNotFoundError")
    try:
        os.rename(base + "/missing", base + "/dst")   # ENOENT on source
    except FileNotFoundError:
        print("rename-missing: FileNotFoundError")
    try:
        os.replace(base + "/missing", base + "/dst")  # ENOENT on source
    except FileNotFoundError:
        print("replace-missing: FileNotFoundError")

    # removedirs stops at the first non-empty parent (rmdir raises -> break)
    os.makedirs(base + "/d1/d2")
    with open(base + "/d1/keep.txt", "w") as fh:
        fh.write("k")
    os.removedirs(base + "/d1/d2")   # removes d2, then rmdir(d1) fails -> break
    print("removedirs-stopped:", exists(base + "/d1"))   # True: d1 kept
    os.remove(base + "/d1/keep.txt")
    os.rmdir(base + "/d1")

    teardown(base)
    print("cleaned:", exists(base))


main()
