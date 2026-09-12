# Error: old-style type alias with unknown lowercase member
from tpy import int32


class Circle:
    radius: int32


Shape = Circle | unknown  # tpyc: error(/Unknown type: unknown/)
