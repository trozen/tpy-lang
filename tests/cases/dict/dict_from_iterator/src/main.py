# Test dict() from user-defined iterator yielding tuple[K, V]
from __future__ import annotations
from tpy import int32

class PairIter:
    current: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.current = 0
        self.limit = limit

    def __iter__(self) -> PairIter:
        return self

    def __next__(self) -> tuple[str, int32]:
        if self.current < self.limit:
            key = str(self.current)
            val = self.current * 10
            self.current += 1
            return (key, val)
        raise StopIteration

def main() -> None:
    d = dict(PairIter(3))
    print(d)
    print(len(d))
    print(d["0"])
    print(d["1"])
    print(d["2"])

main()
