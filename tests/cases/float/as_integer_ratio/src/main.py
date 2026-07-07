# float.as_integer_ratio() -> exact (numerator, denominator) in lowest terms,
# and int.as_integer_ratio() -> (self, 1). Byte-parity with CPython, including
# the inexact-binary case (0.1) and huge exponents; inf/nan raise like CPython.
import math


def main() -> None:
    vals = [0.0, -0.0, 0.5, 1.5, 2.0, 0.1, -0.25, 100.0, 3.141592653589793, 1e300]
    for v in vals:
        num, den = v.as_integer_ratio()
        print(num, den)

    ns: list[int] = [0, 10, -7, 1234567890123456789]
    for n in ns:
        print(n.as_integer_ratio())

    inf = math.inf
    try:
        inf.as_integer_ratio()
    except OverflowError as e:
        print("overflow:", e)

    nan = math.nan
    try:
        nan.as_integer_ratio()
    except ValueError as e:
        print("value:", e)


main()
