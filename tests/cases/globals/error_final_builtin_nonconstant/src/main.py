# Error: builtin function calls are not constant even if they return primitives
from typing import Final
from tpy import int32

BAD: Final[int32] = len("hello")  # tpyc: error(/compile-time constant/)
