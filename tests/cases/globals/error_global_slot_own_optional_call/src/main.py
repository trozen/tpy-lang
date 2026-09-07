# An `Own[T] | None` callee returns STORAGE form, which the global slot write
# would have to lift to a pointer -- a different render.
from tpy import Int32, Own


class Point:
    a: Int32

    def __init__(self, a: Int32) -> None:
        self.a = a


def make(a: Int32) -> Own[Point] | None:
    if a < 0:
        return None
    return Point(a)


hit: Point | None = make(2)  # tpyc: error(/top_level.global_slot_opt_source/)
print(hit is None)
