# A single `except ArithmeticError` catches both ZeroDivisionError and
# OverflowError (CPython's hierarchy). Print a fixed token, not str(e):
# CPython 3.14 rewrote the message text, so pinning it would be
# version-specific; the token still proves the base-class clause caught it.

import math


def main() -> None:
    # Catch ZeroDivisionError via base.
    try:
        x: float = 10.0
        y: float = 0.0
        print(x / y)
    except ArithmeticError:
        print("caught arithmetic: zero division")

    # Catch OverflowError via base.
    try:
        print(int(math.inf))
    except ArithmeticError:
        print("caught arithmetic: overflow")


main()
