# Error: a subclass cannot redeclare a parent's mutable ClassVar as Final.
# The child's read-only slot would conflict with the parent's mutable
# storage in confusing ways -- reject.
from typing import ClassVar, Final
from tpy import Int32


class Parent:
    counter: ClassVar[Int32] = 0


class Child(Parent):
    counter: Final[Int32] = 100  # tpyc: error(/cannot redeclare ClassVar 'counter' from base 'Parent' as Final/)
