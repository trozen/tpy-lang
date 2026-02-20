# Error: old-style type alias with unknown lowercase member
from tpy import Int32


class Circle:
    radius: Int32


Shape = Circle | unknown  # tpyc: error(/Unknown type: unknown/)
