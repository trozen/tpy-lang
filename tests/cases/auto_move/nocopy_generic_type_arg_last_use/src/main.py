# Generic instantiation with nocopy type arg: auto-move at last use
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32):
        self.fd = fd


class Holder[T]:
    item: T

    def __init__(self, item: Own[T]):
        self.item = item


def consume(h: Own[Holder[Handle]]) -> Int32:
    return h.item.fd


def main():
    h = Holder[Handle](Handle(42))
    result = consume(h)  # tpyc: ok
    print(result)


main()
