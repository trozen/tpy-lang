from tpy import Array, Hashable, copy


class Bag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))


class Cell[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = copy(v)


class Keyed[T: Hashable]:
    keys: set[T]

    def __init__(self) -> None:
        self.keys = set()

    def put(self, k: T) -> None:
        self.keys.add(copy(k))


class Mat[T, N: int]:
    a: Array[T, N]

    def __init__(self, a: Array[T, N]) -> None:
        self.a = copy(a)
