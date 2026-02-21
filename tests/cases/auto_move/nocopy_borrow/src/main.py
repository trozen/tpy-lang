# Non-consuming uses (borrow by ref) before consuming move
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def inspect(h: Handle) -> Int32:
    return h.fd


def close(h: Own[Handle]) -> Int32:
    return h.fd


def main():
    h = Handle()
    h.fd = 42
    print(inspect(h))   # borrow (const ref), non-consuming
    print(close(h))     # last use -> auto-move  # tpyc: ok


main()
