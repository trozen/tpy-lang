# readonly[@nocopy] passed to Own[T] param -- sema error (not movable)
from tpy import Int32, Own, nocopy, readonly


@nocopy
class Handle:
    fd: Int32


def close(h: Own[Handle]) -> Int32:
    return h.fd


def inspect(h: readonly[Handle]) -> Int32:
    return close(h)  # tpyc: error(/@nocopy.*cannot be copied into/)


def main():
    h = Handle()
    h.fd = 42
    print(inspect(h))


main()
