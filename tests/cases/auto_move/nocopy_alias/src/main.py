# T& alias of @nocopy -- non-consuming alias access before move is safe.
# Alias is dead before close(h), so auto-move of h proceeds.
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
    print(close(h))  # tpyc: ok


main()
