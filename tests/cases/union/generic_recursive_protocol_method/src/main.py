# A structural protocol method may take and return a generic recursive alias
# instance (Tree[int32]); the protocol signature must be finalized.
from tpy import int32, Own
from typing import Protocol

type Tree[T] = T | list[Tree[T]]


def leaf_count(t: Tree[int32]) -> int32:
    match t:
        case list() as branches:
            n = 0
            for c in branches:
                n += leaf_count(c)
            return n
        case _:
            return 1


class TreeSink(Protocol):
    def absorb(self, t: Tree[int32]) -> int32: ...
    def sprout(self) -> Own[Tree[int32]]: ...


class Counter:
    def absorb(self, t: Tree[int32]) -> int32:
        return leaf_count(t)

    def sprout(self) -> Own[Tree[int32]]:
        return [1, [2, 3]]


def use(s: TreeSink, t: Tree[int32]) -> int32:
    return s.absorb(t) + leaf_count(s.sprout())


def main() -> None:
    c = Counter()
    seed: Tree[int32] = [1, [2, 3], 4]
    print(use(c, seed))


main()
