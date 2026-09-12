# A `*rest` constructor slot has no positional default to fall back on, so an
# omitted-argument construction rejects.
from tpy import int32


class Bag:
    a: int32

    def __init__(self, a: int32, *rest: int32) -> None:
        self.a = a


def make() -> int32:
    x = Bag(1)  # tpyc: error(/expr.call/)
    return x.a


def main() -> None:
    print(make())


main()
