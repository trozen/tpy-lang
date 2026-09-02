from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
from typing import Iterator
def gen() -> Iterator[Int32]:
    q = Point(0)
    for i in range(2):
        p = Point(i)
        q = p
        yield q.x
def main() -> None:
    for v in gen():
        print(v)
main()
