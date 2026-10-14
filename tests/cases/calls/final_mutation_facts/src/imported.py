from typing import overload


class Item:
    def __init__(self, n: int) -> None:
        self.n = n


class Shelf:
    k: int

    def __init__(self) -> None:
        self.k = 0

    # Inferred readonly (self is not written) while it grows its argument.
    def put(self, xs: list[Item]) -> None:
        xs.append(Item(9))

    # Inferred readonly; writes only the elements' fields, never the list.
    def touch_all(self, xs: list[Item]) -> None:
        for x in xs:
            x.n += 1

    # Inferred readonly while it writes its argument's field.
    def bump(self, it: Item) -> None:
        it.n += 1


class Stacker:
    k: int

    def __init__(self) -> None:
        self.k = 0

    # A `typing.overload` stub whose implementation grows its argument.
    @overload
    def put(self, xs: list[Item]) -> None: ...
    def put(self, xs: list[Item]) -> None:
        xs.append(Item(9))
