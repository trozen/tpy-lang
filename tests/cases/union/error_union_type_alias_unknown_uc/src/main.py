# Error: type alias with unknown capitalized member
from tpy import Int32


class Circle:
    radius: Int32


Shape = Circle | UnknownType  # tpyc: error(/Unknown type: UnknownType/)
