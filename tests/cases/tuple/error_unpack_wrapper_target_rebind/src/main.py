# A borrowed recursive-wrapper unpack target that is later rebound rejects:
# its reference alias would write the rebind through into the source, and the
# scalar wrapper alias (`a = s; a = o`) has no pointer-local form either.
from tpy import int32

type Tree[T] = T | list[Tree[T]]


def pair(seed: Tree[int32]) -> tuple[Tree[int32], int32]:
    return (seed, 3)


def main() -> None:
    s: Tree[int32] = [1, 2]
    o: Tree[int32] = [9]
    a, n = pair(s)  # tpyc: error(/not yet supported.*stmt\.tuple_unpack/)
    a = o
    print(n)


main()
