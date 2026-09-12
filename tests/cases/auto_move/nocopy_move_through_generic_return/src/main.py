# Move-through for generic type with nocopy type argument.
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


def transfer() -> Own[Holder[Handle]]:
    h = Holder[Handle](Handle(42))
    alias = h
    return alias


def main():
    r = transfer()
    print(r.item.fd)


main()
