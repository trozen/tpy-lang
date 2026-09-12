# Positional-only params: declared before '/', bind positionally
# with correctly aligned defaults; keyword use of later params works.
from tpy import int32


def f(a: int32, /, b: int32 = 5) -> int32:
    return a * 100 + b


def g(a: int32, b: int32, /, c: int32 = 7) -> int32:
    return a + b + c


class Calc:
    def scale(self, a: int32, /, k: int32 = 2) -> int32:
        return a * k


def main() -> None:
    print(f(7))
    print(f(7, 1))
    print(g(1, 2))
    print(g(1, 2, 3))
    print(Calc().scale(4))
    print(Calc().scale(4, k=3))


main()
