# A generator expression over a user iterable (a class with `__iter__`) is not
# supported yet (BUGS.md#genexpr-over-user-iterable); a comprehension is.
from typing import Iterator


class Bag:
    items: list[str]

    def __init__(self) -> None:
        self.items = ["b", "a"]

    def __iter__(self) -> Iterator[str]:
        return iter(self.items)


def main() -> None:
    b = Bag()
    print(any(s == "a" for s in b))  # tpyc: error(/not yet supported by C\+\+ code generation/)


main()
