# Move-through alias of @nocopy passed to Own[T] param at last use.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32):
        self.fd = fd


def close(h: Own[Handle]) -> Int32:
    return h.fd


def main():
    h = Handle(42)
    alias = h
    print(close(alias))


main()
