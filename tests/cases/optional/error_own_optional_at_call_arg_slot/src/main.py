# An `Own[T] | None` value at a call ARGUMENT slot: `put(h, maybe_make(3))`
# passes an `Own[Point] | None` result as an argument, and one position out
# from the field write there is no argument row for it -- so `put`'s own body
# is only ever reachable at a ctor, where the same field write is pinned by
# tests/cases/auto_move/owned_optional_local_move_out.
from tpy import int32, Own


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y


class Holder:
    value: Point | None

    def __init__(self) -> None:
        self.value = None


def maybe_make(x: int32) -> Own[Point] | None:
    if x > 0:
        return Point(x, x)
    return None


def put(h: Holder, p: Own[Point] | None) -> None:
    h.value = p


def main() -> None:
    h = Holder()
    put(h, maybe_make(3))  # tpyc: error(/call\.arg_shape\.optional/)
    print(h.value is None)


main()
