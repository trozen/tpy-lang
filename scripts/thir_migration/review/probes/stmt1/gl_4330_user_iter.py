from tpy import Int32
from typing import Iterator
class Cyc:
    n: Int32
    def __init__(self) -> None:
        self.n = 0
    def __iter__(self) -> Iterator[Int32]:
        i = 0
        while i < 2:
            yield i
            i += 1
c = Cyc()
it = iter(c)
for v in it:
    print(v)
