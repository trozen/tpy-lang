# Generic non-recursive type alias: declare, pass as param, return,
# use as local annotation. Substitution happens at parse-resolution.
from tpy import Int32

type Pair[T] = tuple[T, T]


def swap(p: Pair[Int32]) -> Pair[Int32]:
    return (p[1], p[0])


def main() -> None:
    p: Pair[Int32] = (Int32(1), Int32(2))  # tpyc: type(/tuple\[Int32, Int32\]/)
    q = swap(p)
    print(p)
    print(q)


main()
