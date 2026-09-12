# Error: type alias referencing unknown type name
from tpy import int32


class Circle:
    radius: int32


type Shape = Circle | unknown  # tpyc: error(/Unknown type/)
