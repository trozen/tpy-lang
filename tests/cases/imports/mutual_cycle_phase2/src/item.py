from tpy import int32, Own


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    items: list[Item]

    def __init__(self, items: Own[list[Item]]) -> None:
        self.items = items
