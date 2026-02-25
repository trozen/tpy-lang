# Return Own where lvalue is alias of generic instantiation with nocopy type arg
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


def bad() -> Own[Holder[Handle]]:
    h = Holder[Handle](Handle(42))
    alias = h
    return alias  # tpyc: error(/non-copyable.*cannot be returned/)


def main():
    r = bad()
    print(r.item.fd)


main()
