# Positional-only params: declared before '/', bind positionally
# with correctly aligned defaults; keyword use of later params works.
from tpy import Int32


def f(a: Int32, /, b: Int32 = 5) -> Int32:
    return a * 100 + b


def g(a: Int32, b: Int32, /, c: Int32 = 7) -> Int32:
    return a + b + c


class Calc:
    def scale(self, a: Int32, /, k: Int32 = 2) -> Int32:
        return a * k


def main() -> None:
    print(f(7))
    print(f(7, 1))
    print(g(1, 2))
    print(g(1, 2, 3))
    print(Calc().scale(4))
    print(Calc().scale(4, k=3))


main()
