# The native record every part of this case binds, and the class pair
# `part_lend_reach` reaches through a field.
from tpy import int32
from tpy.extern import native


@native
class Node:
    v: int32


class Base:
    def __init__(self, v: int32) -> None:
        self.v = v


class Child(Base):
    pass
