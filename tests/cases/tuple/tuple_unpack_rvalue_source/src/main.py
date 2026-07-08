# A value-scalar tuple unpacked directly from a non-name rvalue source: a
# call result (`a, b = make()`) and a field read (`a, b = h.pair`). Both
# capture the rvalue by value (`auto __tup = <expr>;`) rather than the bare
# name's `const auto&` ref-bind, then decl fresh scalar targets.
from tpy import Int32


class Holder:
    pair: tuple[Int32, Int32]

    def __init__(self):
        self.pair = (3, 4)


def make(n: Int32) -> tuple[Int32, Int32]:
    return (n, n + 1)


def from_call(n: Int32) -> Int32:
    a, b = make(n)  # tpyc: ok
    return a + b


def from_field(h: Holder) -> Int32:
    a, b = h.pair  # tpyc: ok
    return a + b


def main() -> None:
    print(from_call(10))
    print(from_field(Holder()))


main()
