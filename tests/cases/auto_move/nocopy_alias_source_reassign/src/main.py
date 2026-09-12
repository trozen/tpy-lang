# Alias source reassigned -- alias points to old slot, move of new source is safe.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def close(h: Own[Handle]) -> int32:
    return h.fd


def main():
    h = Handle()
    h.fd = 42
    alias = h
    print(alias.fd)
    h = Handle()       # reassign h -- alias detached, points to old slot
    h.fd = 99
    print(close(h))    # tpyc: ok (auto-move, alias doesn't constrain)
    print(alias.fd)


main()
