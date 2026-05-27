# Generic alias used inside container types (list element). Confirms
# substitution composes with builtin generic types at every position.
from tpy import Int32

type Pair[T] = tuple[T, T]


def main() -> None:
    pairs: list[Pair[Int32]] = [(Int32(1), Int32(2)), (Int32(3), Int32(4))]  # tpyc: type(/list\[tuple\[Int32, Int32\]\]/)
    for p in pairs:  # tpyc: type(/tuple\[Int32, Int32\]/)
        print(p)


main()
