# Alias created AFTER source reassignment -- alias tracks the current value.
# Moving source must be blocked (alias would dangle).
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def close(h: Own[Handle]) -> Int32:
    return h.fd


def main():
    h = Handle()
    h.fd = 1
    h = Handle()
    h.fd = 42
    alias = h
    print(close(h))    # tpyc: error(/@nocopy.*used after/)
    print(alias.fd)


main()
