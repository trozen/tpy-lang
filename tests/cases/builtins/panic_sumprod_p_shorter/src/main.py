# sumprod raises ValueError when p exhausts before q (the complement of
# panic_sumprod_mismatch, which covers q exhausts first).
import math


def main() -> None:
    math.sumprod([1.0, 2.0], [3.0, 4.0, 5.0])


main()
