# A `with`-hoisted open-T slot reseated from a TERNARY: the reseat arm renders a
# call rvalue whose result is the slot's own type param, not an arbitrary
# expression, so the ternary source rejects.
from tpy import Int32, Own


class Guard:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __enter__(self) -> Int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        pass


class Bag[T]:
    xs: list[T]

    def __init__(self, xs: Own[list[T]]) -> None:
        self.xs = xs

    def pop_one(self) -> Own[T]:
        return self.xs.pop()

    def take(self, n: Int32) -> Own[T]:
        with Guard(n) as g:
            value = self.pop_one() if n > 0 else self.pop_one()  # tpyc: error(/reseat\.opt_storage_source/)
        return value


def main() -> None:
    b = Bag([1, 2, 3])
    print(b.take(1))


main()
