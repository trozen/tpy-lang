# Optional subject: a bare class pattern covers the whole value side, so a
# later value-only arm is unreachable (the None arm would still be fine).
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def f(p: Point | None) -> None:
    match p:
        case Point():
            print("point")
        case Point(x=0):  # tpyc: error(/unreachable case: every non-None value/)
            print("origin")


def main() -> None:
    f(Point(1))


main()
