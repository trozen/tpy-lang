from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
from typing import Iterator
def gen(c: bool) -> Iterator[Int32]:
    if c:
        p = Point(1)
    else:
        p = Point(2)
    for i in range(2):
        yield p.x + i
def main() -> None:
    for v in gen(True):
        print(v)
main()
