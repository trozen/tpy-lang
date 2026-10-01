# A nested def reads a literal-seeded local at the type its stores so far
# give; a later wider store is refused, naming the nested def.
from tpy import int64


def big() -> int:
    return 10 ** 20


def main(seed: int64) -> None:
    n = 0
    n = seed

    def show() -> None:
        print(n)
    show()
    # the store that would widen `n` past the int64 `show` reads
    n = big()  # tpyc: error(/'n' was used as int64 at line 14 \(read by the nested function 'show'\), and this value is int; annotate its first binding: n: int = 0/)
    show()


main(5)
