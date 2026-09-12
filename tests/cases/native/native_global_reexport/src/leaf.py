# A regular (non-native) module declaring a native_global, plus a plain
# Final literal. Both are re-exported through `mid` to exercise the
# variable re-export path in a library module's header.
from typing import Final
from tpy import int32
from tpy.extern import native_global

GLOBAL_VAL: Final[int32] = native_global("tpy_test_global", binding="C")
NORMAL_VAL: Final[int32] = 7
