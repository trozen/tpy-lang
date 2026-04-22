# sumprod raises ValueError on length mismatch.
# Iterator-based impl (in lib/tpy/math.py) drives two iterators and raises
# when one exhausts before the other.
import math


def main() -> None:
    math.sumprod([1.0, 2.0, 3.0], [4.0, 5.0])


main()
