# Generic non-recursive type alias: declare, pass as param, return,
# use as local annotation. Substitution happens at parse-resolution.
from tpy import int32

type Pair[T] = tuple[T, T]


def swap(p: Pair[int32]) -> Pair[int32]:
    return (p[1], p[0])


def main() -> None:
    p: Pair[int32] = (int32(1), int32(2))  # tpyc: type(/tuple\[int32, int32\]/)
    q = swap(p)
    print(p)
    print(q)


main()
