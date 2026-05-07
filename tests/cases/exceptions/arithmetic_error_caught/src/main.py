# ArithmeticError is the base class of both ZeroDivisionError and
# OverflowError, matching CPython's hierarchy. A single
# `except ArithmeticError` block catches either subtype.

import math


def main() -> None:
    # Catch ZeroDivisionError via base.
    try:
        x: float = 10.0
        y: float = 0.0
        print(x / y)
    except ArithmeticError as e:
        print("caught arithmetic:", str(e))

    # Catch OverflowError via base.
    try:
        print(int(math.inf))
    except ArithmeticError as e:
        print("caught arithmetic:", str(e))


main()
