# @total_ordering on a subclass with the anchor (and __eq__) inherited
# from a base class. CPython-faithful: ClassInfo.has_method walks
# parent records via the registry so inherited dunders count.
from functools import total_ordering
from tpy import Int32

class Base:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v
    def __eq__(self, other: "Base") -> bool:
        return self.val == other.val
    def __lt__(self, other: "Base") -> bool:
        return self.val < other.val

@total_ordering
class Child(Base):
    pass

a = Child(Int32(3))
b = Child(Int32(5))
print(a < b)
print(a <= b)
print(a > b)
print(a >= b)
