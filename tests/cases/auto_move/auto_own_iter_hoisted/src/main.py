# Hoisted loop var prevents consuming iteration even at last use.
# When loop var is used after the loop, the container must stay alive.
from __future__ import annotations
from typing import Self, Iterator
from tpy import Int32, auto_own

class IntList:
    items: list[Int32]
    def __init__(self, vals: list[Int32]) -> None:
        self.items = vals

    def __iter__(self: auto_own[Self]) -> auto_own[Iterator[Int32]]:
        return iter(self.items)

def main() -> None:
    xs = IntList([10, 20, 30])
    # Loop var x is used after the loop -- falls back to borrowing
    for x in xs:
        pass
    print(x)

main()
