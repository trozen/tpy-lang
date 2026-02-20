# Error: type alias referencing unknown type name
from tpy import Int32


class Circle:
    radius: Int32


type Shape = Circle | unknown  # tpyc: error(/Unknown type/)
