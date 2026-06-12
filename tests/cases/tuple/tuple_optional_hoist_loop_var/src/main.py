# Hoisted loop variable iterating a storage-form container: the
# storage_form_tuple_locals flag from the outer var-decl must persist
# past the loop, and the rebound var holds the LAST element (CPython).
from tpy import Int32


class T:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def consume(p: tuple[T | None, T | None]) -> None:
    a, _ = p
    if a is not None:
        print(a.x)
    else:
        print("None")


def main() -> None:
    t1 = T(1)
    items: list[tuple[T | None, T | None]] = [(t1, None), (None, None)]

    last = items[0]
    for last in items:
        pass
    consume(last)


main()
