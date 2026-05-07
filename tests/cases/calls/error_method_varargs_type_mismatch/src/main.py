# Wrong element type in a method *args call produces a clean type-mismatch error.
from tpy import Int32


class Sink:
    def consume(self, *xs: Int32) -> None:
        for x in xs:
            print(x)


def main() -> None:
    s = Sink()
    s.consume(Int32(1), "not an int", Int32(3))  # tpyc: error(/\*args element/)


main()
