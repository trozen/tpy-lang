# copy() on generic instantiation with nocopy type arg
from tpy import int32, Own, nocopy, copy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


class Wrapper[T]:
    item: T

    def __init__(self, item: Own[T]):
        self.item = item


def main():
    w = Wrapper[Handle](Handle(1))
    w2 = copy(w)  # tpyc: error(/Cannot copy non-copyable/)
    print(w2.item.fd)


main()
