# readonly[Tree[T]] return follows the reference-type convention: a readonly
# field accessor returns `const Tree<int32_t>&`, and `v = h.view()` binds a
# const reference into the field (no copy). codegen unwraps readonly before
# matching the list-alternative in the wrapper's std::variant. A fresh value
# would instead need Own[Tree[T]].
from tpy import Int32, readonly, Own

type Tree[T] = T | list[Tree[T]]


def leaf_count(t: readonly[Tree[Int32]]) -> Int32:
    match t:
        case list() as branches:
            n = 0
            for c in branches:
                n += leaf_count(c)
            return n
        case _:
            return 1


class Holder:
    t: Tree[Int32]

    def __init__(self, t: Own[Tree[Int32]]) -> None:
        self.t = t

    def view(self) -> readonly[Tree[Int32]]:
        return self.t


def main() -> None:
    h = Holder([1, [2, 3]])
    v = h.view()
    print(leaf_count(v))


main()
