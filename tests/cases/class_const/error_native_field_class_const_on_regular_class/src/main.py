# native_field() on a Final class constant is only allowed on @native classes.
# Exercises the partition-side path in _partition_class_constants (distinct
# from the instance-field path covered by
# tests/cases/native/error_native_field_on_regular_class/).
from tpy.extern import native_field
from typing import Final


class Config:
    FLAG: Final[bool] = native_field("g_flag")  # tpyc: error(/only allowed on @native classes/)
