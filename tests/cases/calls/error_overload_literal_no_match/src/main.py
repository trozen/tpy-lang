# Literal value not in any stub's value set, and no base-type fallback
from typing import Literal, overload
from tpy import Int32


@overload
def process(x: Literal[1, 2]) -> None: ...

@overload
def process(x: Literal[3, 4]) -> None: ...

def process(x: Int32) -> None:
    pass

process(1)   # tpyc: ok
process(5)   # tpyc: error(/No matching/)
