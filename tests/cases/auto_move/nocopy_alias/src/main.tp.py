# T& alias of @nocopy -- non-consuming alias access before move is safe.
# NOTE: alias used AFTER close(h) would be dangling (Phase 5 will catch this).
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def close(h: Own[Handle]) -> Int32:
    return h.fd


def main():
    h = Handle()
    h.fd = 42
    alias = h
    print(alias.fd)
    print(close(h))  # tpyc: ok


main()
