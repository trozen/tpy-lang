from typing import ClassVar
from tpy import int32
class Counter:
    PAIR: ClassVar[tuple[int32, int32]] = (1, 2)
    def __init__(self) -> None:
        pass
def a1() -> tuple[int32, int32]:
    return Counter.PAIR
def a2() -> None:
    t = Counter.PAIR
    print(t[0])
a2()
print(a1()[0])
