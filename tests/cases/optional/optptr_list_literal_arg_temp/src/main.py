# A list literal at an `Optional[list]` parameter renders through the SPELLED
# container ctor in its argument temp, not the bare decl-slot brace. The
# receiver record is mutated through the call and read back after it.
from tpy import Int32


class Sink:
    n: Int32

    def __init__(self) -> None:
        self.n = 0

    def take(self, xs: list[Int32] | None) -> Int32:
        if xs is not None:
            self.n += len(xs)
        return self.n

    def take_nested(self, xs: list[list[Int32]] | None) -> Int32:
        if xs is not None:
            self.n += len(xs)
        return self.n


def fill(s: Sink) -> None:
    print(s.take([1, 2, 3]))  # list literal -> spelled-ctor argument temp
    print(s.take_nested([[4], [5, 6]]))


def main() -> None:
    s = Sink()
    fill(s)
    print(s.n)


main()
