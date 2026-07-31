# A reassigned Optional-field pointer local ALIASES the field storage:
# mutating through the rebound local is visible on the holder (not a copy).
from typing import Optional
from tpy import Int32, copy


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Holder:
    value: Optional[Point]

    def __init__(self) -> None:
        self.value = None


def test(h: Holder) -> None:
    p: Point | None = h.value
    print(p is None)
    h.value = copy(Point(1))
    p = h.value
    if p is not None:
        p.x = 42  # mutate through the alias -- visible on h.value
    v = h.value
    if v is not None:
        print(v.x)


def main() -> None:
    test(Holder())


main()
