# Warning when __next__ has no raise StopIteration
from __future__ import annotations
from tpy import int32

class InfiniteCounter:
    current: int32

    def __init__(self) -> None:
        self.current = 0

    def __iter__(self) -> InfiniteCounter:
        return self

    # tpyc: warning(/no 'raise StopIteration'/)
    def __next__(self) -> int32:
        val = self.current
        self.current += 1
        return val

def main() -> None:
    c = InfiniteCounter()
    count: int32 = 0
    for x in c:
        print(x)
        count += 1
        if count >= 3:
            break

main()
