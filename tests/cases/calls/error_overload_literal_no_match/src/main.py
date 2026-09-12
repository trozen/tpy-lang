# Literal value not in any stub's value set, and no base-type fallback.
# The MATCHING call into such a fallback-less literal-only group is pinned
# by tests/cases/calls/overload_literal_int.
from typing import Literal, overload
from tpy import int32


@overload
def process(x: Literal[1, 2]) -> None: ...

@overload
def process(x: Literal[3, 4]) -> None: ...

def process(x: int32) -> None:
    pass

process(1)
process(5)   # tpyc: error(/No matching/)
