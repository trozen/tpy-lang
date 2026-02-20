# Error: type alias with unknown capitalized member (caught at sema, not C++)
from tpy import Int32


class Circle:
    radius: Int32


Shape = Circle | UnknownType  # tpyc: error(/unknown type 'UnknownType'/)
