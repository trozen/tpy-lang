# raise X(Bag(...)) where the ctor mutates its ref param and the arg is a
# temporary -- the shared loop binds it to a named temp (rvalue can't bind Bag&).
class Bag:
    items: list[int]

    def __init__(self, items: list[int]) -> None:
        self.items = items

    def push(self, v: int) -> None:
        self.items.append(v)


class BErr(Exception):
    total: int

    def __init__(self, b: Bag) -> None:
        super().__init__("b")
        b.push(99)
        self.total = len(b.items)


def main() -> None:
    try:
        raise BErr(Bag([1, 2]))
    except BErr as e:
        print(e.total)


main()
