# A module-level union global initialized to None: the global is a POINTER to the
# variant, so writing the monostate through it would dereference a null slot --
# the union monostate arms need their own render, and rejecting is the safe half.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Line:
    a: Point

    def __init__(self) -> None:
        self.a = Point(0)


g: Point | Line | None = None  # tpyc: error(/not yet supported.*global_slot_shape/)
print(g is None)
