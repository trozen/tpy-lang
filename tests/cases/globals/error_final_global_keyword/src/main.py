# Error: cannot use 'global' with Final variable
from typing import Final
from tpy import Int32

X: Final[Int32] = 42

def foo() -> None:
    global X  # tpyc: error(/Cannot use 'global' with Final/)

foo()
