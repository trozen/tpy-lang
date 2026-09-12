from typing import Final
from tpy import int32


class Parent:
    LIMIT: Final[int32] = 10


class Child(Parent):
    pass
