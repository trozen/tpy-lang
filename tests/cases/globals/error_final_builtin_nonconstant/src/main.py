# Error: builtin function calls are not constant even if they return primitives
from typing import Final
from tpy import Int32

BAD: Final[Int32] = len("hello")  # tpyc: error(/compile-time constant/)
