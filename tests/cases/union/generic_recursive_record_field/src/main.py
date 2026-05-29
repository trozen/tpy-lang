# A record can store a generic recursive alias instance (Tree[Int32]) as a
# by-value field, take one as a method parameter, and return one from a method.
from tpy import Int32, Own

type Tree[T] = T | list[Tree[T]]


def leaf_count(t: Tree[Int32]) -> Int32:
    match t:
        case list() as branches:
            total = 0
            for child in branches:
                total += leaf_count(child)
            return total
        case _:
            return 1


class Holder:
    t: Tree[Int32]

    def __init__(self, t: Own[Tree[Int32]]) -> None:
        self.t = t

    def get(self) -> Tree[Int32]:
        return self.t

    def matches(self, other: Tree[Int32]) -> bool:
        return leaf_count(self.t) == leaf_count(other)


def main() -> None:
    seed: Tree[Int32] = [1, [2, 3], 4]
    h = Holder(seed)
    g = h.get()
    print(leaf_count(g))
    print(leaf_count(h.get()))
    probe: Tree[Int32] = [9, 9, 9, 9]
    print(h.matches(probe))


main()
