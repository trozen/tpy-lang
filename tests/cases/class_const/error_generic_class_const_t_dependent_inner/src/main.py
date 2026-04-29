# Error: T-dependent class constant -- the inner type references the class's
# type parameter, which would require per-monomorphization codegen. Deferred
# to a future extension; Phase 9 supports T-independent constants only.
from typing import Final


class Box[T]:
    DEFAULT: Final[T] = 0  # tpyc: error(/class constant 'DEFAULT' on generic class 'Box' references type parameter 'T' in its declared type/)
