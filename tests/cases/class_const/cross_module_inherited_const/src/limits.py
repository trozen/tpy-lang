from typing import Final
from tpy import Int32


class Parent:
    LIMIT: Final[Int32] = 10


class Child(Parent):
    pass
