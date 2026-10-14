# Declares a non-value native_global that `mid` re-exports.
from tpy import int32
from tpy.extern import native_global

xs: list[int32] = native_global("tpy_test_xs")
