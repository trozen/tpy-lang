# Error: type alias with unknown capitalized member
from tpy import int32


class Circle:
    radius: int32


Shape = Circle | UnknownType  # tpyc: error(/Unknown type: UnknownType/)
