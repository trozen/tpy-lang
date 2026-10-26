# A construction through a `*rest` constructor slot, here with the pack
# omitted, is refused at the call (BUGS.md#ctor-varargs-construction-unlowered).
from tpy import int32


class Bag:
    a: int32

    def __init__(self, a: int32, *rest: int32) -> None:
        self.a = a


def make() -> int32:
    x = Bag(1)  # tpyc: error(/through its \*args parameter is not supported yet/)
    return x.a


def main() -> None:
    print(make())


main()
