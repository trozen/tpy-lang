# A nested def cannot complete its enclosing body's pending instance: refused
# as uninferred (BUGS.md#nested-def-completes-pending-local-crash).
from tpy import copy, int32


class Bag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))


class C:
    def m(self) -> int32:
        b = Bag()

        def inner() -> None:
            b.put(4)  # tpyc: error(/Cannot infer type arguments for 'Bag'\. Please specify explicitly: Bag\[T\]/)
        inner()
        return len(b.items)


def main() -> None:
    print(C().m())


main()
