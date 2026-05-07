# list[tuple[T | None, ...]]: list elements stored as storage-form tuples.
# Tuple literals at the list-literal site are lifted via tuple_to_storage
# during list initialization. Subscript reads in pointer-form param context
# go through tuple_to_pointer.
from tpy import Int32


class T:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def consume(p: tuple[T | None, T | None]) -> None:
    a, b = p
    if a is not None:
        print(a.x)
    else:
        print("None")


def main() -> None:
    t1 = T(1)
    t2 = T(2)
    items: list[tuple[T | None, T | None]] = [(t1, t2), (t1, None), (None, None)]

    # Subscript read from a list of storage-form tuples flows through
    # tuple_to_pointer to match the pointer-form param of consume().
    consume(items[0])
    consume(items[1])
    consume(items[2])

    # Destructuring directly from a subscript also works.
    a, b = items[0]
    if a is not None:
        print(a.x)


main()
