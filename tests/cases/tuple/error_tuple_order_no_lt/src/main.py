# Error: tuple ordering where element type has __eq__ but no __lt__
from __future__ import annotations

class Foo:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v

    def __eq__(self, other: Foo) -> bool:
        return self.val == other.val

def main() -> None:
    a = (Foo(1),)
    b = (Foo(2),)
    print(a < b)  # tpyc: error(/__lt__/)

main()
