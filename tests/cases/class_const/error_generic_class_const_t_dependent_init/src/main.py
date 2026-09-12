# Error: T-dependent class constant -- the initializer references the
# class's type parameter (e.g. `T()` for a per-instantiation default).
# Deferred to a future extension.
from typing import Final
from tpy import int32


class Box[T]:
    DEFAULT: Final[int32] = T()  # tpyc: error(/class constant 'DEFAULT' on generic class 'Box' references type parameter 'T' in its initializer/)
