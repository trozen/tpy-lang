"""Test static overflow check for Int32 literals."""
from tpy import Int32

max_ok: Int32 = 2147483647    # tpyc: ok
min_ok: Int32 = -2147483648   # tpyc: ok
too_big: Int32 = 3000000000   # tpyc: error(/outside Int32 range/)
# TODO: do not stop at first error and make this work
# too_small: Int32 = -3000000000  # tpyc: error(/outside Int32 range/)
