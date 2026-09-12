# Owning rvalue elements in a tuple-literal unpack must MOVE out of their
# hidden temp into the targets (the targets own), not dangle as aliases to
# a dead temp. Uses @nocopy Box so a silent copy would be a compile error.
from tpy import int32, Own
from tplib.box import Box


def make(v: int32) -> Own[Box[int32]]:
    return Box(v)


def pair() -> tuple[Own[Box[int32]], Own[Box[int32]]]:
    a, b = (make(7), make(9))
    return (a, b)


def main() -> None:
    x, y = pair()
    print(x.get())
    print(y.get())


main()
