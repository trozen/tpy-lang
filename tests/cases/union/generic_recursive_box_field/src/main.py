# A generic recursive alias instance stored inside a Box inside a generic
# class: Holder[T] with a Box[Tree[T]] field. Pins the instance as a Box
# element and as a generic-class field (storage form), constructed and read
# back through the box.
from tpy import Int32, Own
from tplib.box import Box

type Tree[T] = T | list[Tree[T]]


class Holder[T]:
    data: Box[Tree[T]]

    def __init__(self, data: Own[Box[Tree[T]]]) -> None:
        self.data = data


def leaf_count[T](t: Tree[T]) -> Int32:
    match t:
        case list() as branches:
            total = 0
            for child in branches:
                total += leaf_count(child)
            return total
        case _:
            return 1


def main() -> None:
    tree: Tree[int] = [1, [2, 3], 4]
    # Explicit Holder[int]: ctor inference does not yet deduce T through a
    # nested Box[Tree[T]] arg (see BUGS.md).
    h: Holder[int] = Holder(Box(tree))
    print(leaf_count(h.data.get()))


main()
