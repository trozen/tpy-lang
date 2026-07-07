# A data-carrying user exception with a container field can't cross the CPython
# boundary yet: the attribute-marshal path has no container emit (deferred).
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


class ParseError(ValueError):
    items: list[Int64]  # tpyc: error(/data field 'items' of type list\[Int64\] cannot cross/)

    def __init__(self, message: str, items: list[Int64]):
        self.message = message
        self.items = items


@export
def parse(n: Int64) -> Int64:
    return n
