# Error: Final can only be used at module level
from typing import Final
from tpy import Int32

def foo() -> None:
    X: Final[Int32] = 42  # tpyc: error(/module level/)

foo()
