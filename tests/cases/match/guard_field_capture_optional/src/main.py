# Optional record subject: a guarded class arm whose guard reads a FIELD
# capture must emit the binding before the guard; a failed guard falls
# through to the bare class arm.
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def f(p: Point | None) -> None:
    match p:
        case None:
            print("none")
        case Point(x=n) if n > 10:
            print("big", n)
        case Point(x=n):
            print("small", n)


def main() -> None:
    f(Point(50))
    f(Point(2))
    f(None)


main()
