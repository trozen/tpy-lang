# A data-carrying user exception with a container field can't cross the CPython
# boundary yet: the attribute-marshal path has no container emit (deferred).
# tpy: ext_module
from tpy import int64
from tpy.extern import export


class ParseError(ValueError):
    items: list[int64]  # tpyc: error(/data field 'items' of type list\[int64\] cannot cross/)

    def __init__(self, message: str, items: list[int64]):
        super().__init__(message)
        self.items = items


@export
def parse(n: int64) -> int64:
    return n
