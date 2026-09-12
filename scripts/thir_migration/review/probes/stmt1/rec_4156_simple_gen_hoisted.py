from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
from typing import Iterator
def gen() -> Iterator[int32]:
    q = Point(0)
    for i in range(2):
        p = Point(i)
        q = p
        yield q.x
def main() -> None:
    for v in gen():
        print(v)
main()
