# Every spelling of an import that names a real `tpy` export still binds now
# that the import itself is checked: an alias, a parser keyword, a function,
# a parenthesized multi-line list, `Throwable`, and a typing name beside them.
from typing import Optional  # tpyc: ok
from tplib import Box
from tpy import int32 as I  # tpyc: ok
from tpy import auto_readonly, copy  # tpyc: ok
from tpy import (  # tpyc: ok
    Throwable,
    uint8,
)


class Bag:
    items: list[int]

    def __init__(self) -> None:
        self.items = [1, 2]

    # auto_readonly: a parser-keyword export applied as a decorator.
    @auto_readonly
    def view(self) -> auto_readonly[list[int]]:
        return self.items


def find(xs: list[I], v: I) -> Optional[I]:
    for x in xs:
        if x == v:
            return x
    return None


def main() -> None:
    # alias: `I` is int32 under its local name.
    n: I = 7
    print("alias", n + 1)

    # keyword: the view aliases the field, so a write through it shows.
    bag = Bag()
    bag.view().append(3)
    print("keyword", bag.items)

    # function: copy() is the explicit duplicate.
    ys = copy(bag.items)
    ys.append(9)
    print("function", bag.items, ys)

    # multi-line: a name from the parenthesized list.
    b: uint8 = 250
    print("multi-line", b + 5)

    # typing: Optional from typing beside the tpy imports.
    xs: list[I] = [1, 2]
    print("typing", find(xs, 2), find(xs, 5))

    # throwable: the exception root names the element of exception storage.
    stored: list[Box[Throwable]] = []
    print("throwable", len(stored))


main()
