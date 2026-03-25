# Error: yield in __init__ is not allowed
from typing import Iterator
from tpy import Int32

class Bad:
    value: Int32
    def __init__(self, v: Int32) -> None:  # tpyc: error(/'__init__' cannot be a generator/)
        self.value = v
        yield v
