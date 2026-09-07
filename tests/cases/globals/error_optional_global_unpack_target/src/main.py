# A ptr-repr Optional GLOBAL as an unpack target: its slot carries the inner
# spelling plus a pointer lift, which the unpack targets do not spell.
from tpy import Int32, Own


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def make_opt() -> tuple[Int32, Own[Point | None]]:
    return (1, Point(2))


n, opt = make_opt()  # tpyc: error(/stmt.tuple_unpack/)
print(n)
if opt is not None:
    print(opt.x)
