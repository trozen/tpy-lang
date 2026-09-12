# Inherited __next__() -> int32 should NOT satisfy Iterator[str]
from __future__ import annotations
from typing import Iterator
from tpy import int32

class Counter:
    current: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.current = 0
        self.limit = limit

    def __iter__(self) -> Counter:
        return self

    def __next__(self) -> int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

class DoubleCounter(Counter):
    def __init__(self, limit: int32) -> None:
        super().__init__(limit * 2)

def consume_str(it: Iterator[str]) -> None:
    pass

consume_str(DoubleCounter(3))  # tpyc: error(/does not conform to protocol/)
