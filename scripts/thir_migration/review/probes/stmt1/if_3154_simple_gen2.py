from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
from typing import Iterator
def gen(c: bool) -> Iterator[int32]:
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
