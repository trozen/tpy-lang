# os filesystem error mapping: getsize / listdir / chdir on a missing path
# raise FileNotFoundError (an OSError subclass), matching CPython. Only the
# exception TYPE is observed; message text differs from CPython.
import os
from os.path import getsize


def main():
    try:
        getsize("tpy_nope_missing_ospath")
    except FileNotFoundError:
        print("getsize: FileNotFoundError")

    try:
        os.listdir("tpy_nope_missing_dir_xyz")
    except FileNotFoundError:
        print("listdir: FileNotFoundError")

    try:
        os.chdir("tpy_nope_missing_dir_xyz")
    except FileNotFoundError:
        print("chdir: FileNotFoundError")

    # listdir through a non-directory: ENOTDIR. CPython raises
    # NotADirectoryError (an OSError); TPy raises OSError -- caught by both.
    with open("tpy_fs_errors_file.txt", "w") as fh:
        fh.write("x")
    try:
        os.listdir("tpy_fs_errors_file.txt")
    except OSError:
        print("listdir-on-file: OSError")

    try:
        getsize("tpy_nope_missing_ospath")
    except OSError:
        print("getsize: OSError base")


main()
