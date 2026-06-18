# os.stat / os.path.getmtime / samefile on a missing path raise
# FileNotFoundError (matching CPython). Only the exception TYPE is observed.
import os
from os.path import getmtime, samefile


def main():
    missing = "/tmp/tpy_os_stat_missing_xyz"
    try:
        os.stat(missing)
    except FileNotFoundError:
        print("stat: FileNotFoundError")
    try:
        os.lstat(missing)
    except FileNotFoundError:
        print("lstat: FileNotFoundError")
    try:
        getmtime(missing)
    except FileNotFoundError:
        print("getmtime: FileNotFoundError")
    try:
        samefile(missing, missing)
    except FileNotFoundError:
        print("samefile: FileNotFoundError")
    try:
        os.stat(missing)
    except OSError:
        print("stat: OSError base")


main()
