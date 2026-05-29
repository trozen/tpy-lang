# A record constructor parameter typed as a generic recursive alias instance
# (Tree[Int32]) must accept a Tree[Int32] argument. Stores a derived scalar so
# the wrapper struct is not embedded in the record.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def count_leaves(t: Tree[Int32]) -> Int32:
    match t:
        case list() as branches:
            n = 0
            for c in branches:
                n += count_leaves(c)
            return n
        case _:
            return 1


class Summary:
    n: Int32

    def __init__(self, t: Tree[Int32]) -> None:
        self.n = count_leaves(t)


def main() -> None:
    seed: Tree[Int32] = [1, [2, 3], 4]
    s = Summary(seed)  # tpyc: ok
    print(s.n)
    leaf: Tree[Int32] = 7
    print(Summary(leaf).n)  # tpyc: ok


main()
