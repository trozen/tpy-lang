from __future__ import annotations
from tpy import Int32

class Iter:
    def __iter__(self) -> Iter:
        return self

    def __next__(self) -> Int32:
        raise StopIteration("message")  # tpyc: error(/does not accept arguments/)
