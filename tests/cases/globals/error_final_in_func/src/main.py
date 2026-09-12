# Error: Final can only be used at module level
from typing import Final
from tpy import int32

def foo() -> None:
    X: Final[int32] = 42  # tpyc: error(/module level/)

foo()
