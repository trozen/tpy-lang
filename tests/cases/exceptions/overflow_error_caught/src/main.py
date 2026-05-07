# Runtime OverflowError is raised by float-to-int conversion when the source
# is positive or negative infinity. NaN routes to ValueError (covered by
# value_error_caught). Both messages match CPython.

import math


def main() -> None:
    # +inf -> int (BigInt path).
    try:
        print(int(math.inf))
    except OverflowError as e:
        print("caught:", str(e))

    # -inf -> int.
    try:
        print(int(-math.inf))
    except OverflowError as e:
        print("caught:", str(e))

    # ArithmeticError catches OverflowError too.
    try:
        print(int(math.inf))
    except ArithmeticError as e:
        print("via ArithmeticError:", str(e))


main()
