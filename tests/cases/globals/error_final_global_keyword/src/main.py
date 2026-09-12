# Error: cannot use 'global' with Final variable
from typing import Final
from tpy import int32

X: Final[int32] = 42

def foo() -> None:
    global X  # tpyc: error(/Cannot use 'global' with Final/)

foo()
