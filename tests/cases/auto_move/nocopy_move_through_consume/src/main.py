# Move-through alias of @nocopy passed to Own[T] param at last use.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


def close(h: Own[Handle]) -> int32:
    return h.fd


def main():
    h = Handle(42)
    alias = h
    print(close(alias))


main()
