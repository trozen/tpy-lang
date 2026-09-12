# Alias created AFTER source reassignment -- alias tracks the current value.
# Moving source must be blocked (alias would dangle).
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def close(h: Own[Handle]) -> int32:
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
