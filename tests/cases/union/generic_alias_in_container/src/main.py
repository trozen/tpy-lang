# Generic alias used inside container types (list element). Confirms
# substitution composes with builtin generic types at every position.
from tpy import int32

type Pair[T] = tuple[T, T]


def main() -> None:
    pairs: list[Pair[int32]] = [(int32(1), int32(2)), (int32(3), int32(4))]  # tpyc: type(/list\[tuple\[int32, int32\]\]/)
    for p in pairs:  # tpyc: type(/tuple\[int32, int32\]/)
        print(p)


main()
