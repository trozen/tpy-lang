# Alias composition: a non-generic alias refers to a generic alias's
# instantiation. Substitution chains through naturally.
from tpy import Int32

type Pair[T] = tuple[T, T]
type IntPair = Pair[Int32]


def main() -> None:
    p: IntPair = (Int32(3), Int32(7))  # tpyc: type(/tuple\[Int32, Int32\]/)
    print(p)


main()
