from typing import ClassVar
from tpy import Int32
class Counter:
    PAIR: ClassVar[tuple[Int32, Int32]] = (1, 2)
    def __init__(self) -> None:
        pass
def a1() -> tuple[Int32, Int32]:
    return Counter.PAIR
def a2() -> None:
    t = Counter.PAIR
    print(t[0])
a2()
print(a1()[0])
