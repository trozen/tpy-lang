# A (float, bool) tuple local rebound to math.frexp is refused: the bool
# element does not pick frexp's integer exponent type.
import math


def rebind(c: bool) -> None:
    p = (0.5, c)
    p = math.frexp(8.0)  # tpyc: error(/Type mismatch in reassignment to 'p' \(tuple element 1\): expected bool, got int32/)
    print(p)


rebind(True)
