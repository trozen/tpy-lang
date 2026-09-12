# Wrong element type in a method *args call produces a clean type-mismatch error.
from tpy import int32


class Sink:
    def consume(self, *xs: int32) -> None:
        for x in xs:
            print(x)


def main() -> None:
    s = Sink()
    s.consume(int32(1), "not an int", int32(3))  # tpyc: error(/\*args element/)


main()
