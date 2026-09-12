# Error: yield in __init__ is not allowed
from typing import Iterator
from tpy import int32

class Bad:
    value: int32
    def __init__(self, v: int32) -> None:  # tpyc: error(/'__init__' cannot be a generator/)
        self.value = v
        yield v
