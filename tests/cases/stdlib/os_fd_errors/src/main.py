# Error paths for fd I/O + metadata: open a missing path, read/fstat a bad fd,
# chmod/utime a missing path -- each raises the expected OSError subclass
# (caught, so output is deterministic). Byte-compared against CPython.
import os

_MISSING = "tpy_nope_missing_xyz"


def main():
    if os.path.exists(_MISSING):
        os.remove(_MISSING)

    try:
        os.open(_MISSING, os.O_RDONLY)
    except FileNotFoundError:
        print("open: FileNotFoundError")

    try:
        os.read(99999, 4)            # bad fd
    except OSError:
        print("read: OSError")

    try:
        os.fstat(99999)
    except OSError:
        print("fstat: OSError")

    try:
        os.chmod(_MISSING, 0o600)
    except FileNotFoundError:
        print("chmod: FileNotFoundError")

    try:
        os.utime(_MISSING, (1.0, 1.0))
    except FileNotFoundError:
        print("utime: FileNotFoundError")


main()
