from typing import ClassVar, Optional
from tpy import Int32
class Counter:
    LIMIT: ClassVar[Int32] = 10
    PAIR: ClassVar[tuple[Int32, Int32]] = (1, 2)
    def __init__(self) -> None:
        pass
class H:
    c: Optional[Counter]
    def __init__(self) -> None:
        self.c = Counter()
def a1(h: H) -> Int32:
    return h.c.LIMIT
def a3() -> None:
    print(Counter.PAIR[0])
def a4() -> None:
    print(Counter.PAIR)
def a5() -> Int32:
    a, b = Counter.PAIR
    return a + b
def main() -> None:
    print(a1(H()), a5())
    a3(); a4()
main()
