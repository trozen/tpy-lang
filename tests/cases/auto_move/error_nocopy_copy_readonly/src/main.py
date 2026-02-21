# copy() on readonly[@nocopy] must error in sema, not C++ backend
from tpy import Int32, Own, nocopy, copy, readonly


@nocopy
class Handle:
    fd: Int32


def inspect(h: readonly[Handle]) -> Own[Handle]:
    return copy(h)  # tpyc: error(/Cannot copy @nocopy/)


def main():
    h = Handle()
    h.fd = 42
    h2 = inspect(h)
    print(h2.fd)


main()
