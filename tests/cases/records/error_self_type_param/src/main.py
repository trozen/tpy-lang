# Self type error: cannot use Self in free function parameter
from typing import Self

def bad_param(x: Self) -> None:  # tpyc: error(/Self type cannot be used in function parameter/)
    pass
