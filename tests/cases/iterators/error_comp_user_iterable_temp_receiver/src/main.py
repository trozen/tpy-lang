# A comprehension over a user iterable a call returns by reference is rejected:
# it may lend a dying temporary (BUGS.md#borrow-call-comp-source).
from typing import Iterator


class Bag:
    items: list[str]

    def __init__(self) -> None:
        self.items = ["b", "a"]

    def __iter__(self) -> Iterator[str]:
        return iter(self.items)


class Holder:
    bag: Bag

    def __init__(self) -> None:
        self.bag = Bag()

    def get(self) -> Bag:
        return self.bag


def main() -> None:
    print([s + "!" for s in Holder().get()])  # tpyc: error(/not yet supported by C\+\+ code generation/)


main()
