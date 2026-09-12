# An `Own[T] | None` return is a MATERIALIZED storage optional, so the checked
# deref (which takes a raw pointer) has no row for it. Concretely,
# `make_maybe(True).x` accesses a field straight off the call's optional
# return; TPy rejects that access today.
from tpy import int32, Own


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def make_maybe(flag: bool) -> Own[Point | None]:
    if flag:
        return Point(5)
    return None


def main() -> None:
    print(make_maybe(True).x)  # tpyc: error(/field.receiver_shape/)


main()
