# dict() constructor from inherited iterator yielding key-value tuples
from __future__ import annotations
from tpy import Int32

class PairIter:
    current: Int32
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.current = 0
        self.limit = limit

    def __iter__(self) -> PairIter:
        return self

    def __next__(self) -> tuple[str, Int32]:
        if self.current < self.limit:
            val = self.current
            self.current += 1
            return (str(val), val * 10)
        raise StopIteration

class DoublePairIter(PairIter):
    def __init__(self, limit: Int32) -> None:
        super().__init__(limit * 2)

def main() -> None:
    d = dict(DoublePairIter(2))
    print(d)
    print(len(d))

main()
