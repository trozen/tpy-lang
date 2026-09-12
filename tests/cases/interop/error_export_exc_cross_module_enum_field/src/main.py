# A user exception data field typed as an exposed enum imported from ANOTHER
# ext_module is a located error, not a misbuild: the enum's CPython type handle
# lives in the defining module's glue, so the setter here has nothing to
# reference.
# tpy: ext_module
from tpy import int64
from tpy.extern import export
from enum_mod import Color


class Bad(Exception):
    c: Color  # tpyc: error(/data field 'c' is an exposed enum from another module/)

    def __init__(self, message: str, c: Color):
        self.message = message
        self.c = c


@export
def f(n: int64) -> int64:
    return n
