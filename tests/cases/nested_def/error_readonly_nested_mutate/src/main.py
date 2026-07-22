# A nested def cannot mutate self inside a declared @readonly method -- the
# readonly-scope enforcement reaches closure bodies.
from tpy import Int32, readonly


class C:
    n: Int32

    def __init__(self) -> None:
        self.n = 0

    @readonly
    def bad(self) -> None:
        def poke() -> None:
            self.n = 5  # tpyc: error(/Cannot mutate readonly reference/)

        poke()


def main() -> None:
    c = C()
    c.bad()


main()
