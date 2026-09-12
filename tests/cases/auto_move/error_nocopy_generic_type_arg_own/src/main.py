# Own param with generic instantiation containing nocopy type arg, not at last use
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


class Holder[T]:
    item: T

    def __init__(self, item: Own[T]):
        self.item = item


def consume(h: Own[Holder[Handle]]) -> int32:
    return h.item.fd


def main():
    h = Holder[Handle](Handle(42))
    result = consume(h)  # tpyc: error(/non-copyable.*used after/)
    print(h.item.fd)


main()
