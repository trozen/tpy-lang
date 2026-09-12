# Alias composition: a non-generic alias refers to a generic alias's
# instantiation. Substitution chains through naturally.
from tpy import int32

type Pair[T] = tuple[T, T]
type IntPair = Pair[int32]


def main() -> None:
    p: IntPair = (int32(3), int32(7))  # tpyc: type(/tuple\[int32, int32\]/)
    print(p)


main()
