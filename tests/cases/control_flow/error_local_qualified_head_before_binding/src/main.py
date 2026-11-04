# The head of a qualified read names a local the function binds later: the
# read is that unassigned local (CPython: UnboundLocalError), never the
# module's enum.
from enum import Enum


class Color(Enum):
    RED = 1
    BLUE = 2


def f() -> None:
    c = Color.RED  # tpyc: error(/'Color' is read before it is bound on line 16: .* does not see the module's 'Color'/)
    print(c)
    if False:
        Color = 3


f()
