# A wrapper field accessor returns `Tree<int32_t>&`, and a `match h.get()`
# subject binds it by reference, so a mutation in the matched arm reaches the
# field (CPython aliasing): both TPy and CPython print 3.
from tpy import int32, Own

type Tree[T] = T | list[Tree[T]]


def count(t: Tree[int32]) -> int32:
    match t:
        case list() as branches:
            n = 0
            for c in branches:
                n += count(c)
            return n
        case _:
            return 1


class Holder:
    t: Tree[int32]

    def __init__(self, t: Own[Tree[int32]]) -> None:
        self.t = t

    def get(self) -> Tree[int32]:
        return self.t


def main() -> None:
    h = Holder([1, 2])
    match h.get():
        case list() as got:
            got.append(3)
        case _:
            pass
    # The match subject binds by reference, so the append reaches h's tree: 3.
    print(count(h.get()))


main()
