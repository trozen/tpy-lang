# A `*rest` constructor slot has no positional default to fall back on, so an
# omitted-argument construction rejects.
from tpy import Int32


class Bag:
    a: Int32

    def __init__(self, a: Int32, *rest: Int32) -> None:
        self.a = a


def make() -> Int32:
    x = Bag(1)  # tpyc: error(/expr.call/)
    return x.a


def main() -> None:
    print(make())


main()
