# Self type errors: invalid usage in free functions and static methods
from typing import Self
from tpy import Int32

def bad_function() -> Self:  # tpyc: error(/Self type cannot be used as a return type/)
    pass
