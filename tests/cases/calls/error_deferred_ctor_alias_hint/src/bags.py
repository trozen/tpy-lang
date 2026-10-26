from tpy import copy


class Bag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))
