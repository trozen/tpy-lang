# Two-argument math.log(x, base) -- motivating use case for bodied @overload
import math


def approx(x: float, target: float) -> bool:
    d = x - target
    if d < 0.0:
        d = -d
    return d < 0.001


def main() -> None:
    # log(8, 2) should be ~3.0
    if approx(math.log(8.0, 2.0), 3.0):
        print("log(8, 2) ok")
    else:
        print("log(8, 2) error")

    # log(100, 10) should be ~2.0
    if approx(math.log(100.0, 10.0), 2.0):
        print("log(100, 10) ok")
    else:
        print("log(100, 10) error")

    # Single-arg form still works (natural log of e)
    if approx(math.log(2.718281828), 1.0):
        print("log(e) ok")
    else:
        print("log(e) error")


main()
