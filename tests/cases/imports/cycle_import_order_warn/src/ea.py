# `ea` starts `eb` and `fe` before it defines anything they read of it.
import eb
import fe
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Proto(Protocol):
    def f(self) -> int: ...


class Color(Enum):
    RED = 1
    BLUE = 2


class Item:
    def __init__(self, n: int) -> None:
        self.n = n


@dataclass
class Pt:
    x: int = 0


def main() -> None:
    it = Item(5)
    eb.bump(it)
    # The object main holds: `bump` took it by reference.
    print("item:", it.n)
    p = Pt(1)
    eb.shift(p)
    print("decorated:", p.x)
    eb.main()
    fe.main()
