from tpy import int32
from typing import Iterator
class Cyc:
    n: int32
    def __init__(self) -> None:
        self.n = 0
    def __iter__(self) -> Iterator[int32]:
        i = 0
        while i < 2:
            yield i
            i += 1
c = Cyc()
it = iter(c)
for v in it:
    print(v)
