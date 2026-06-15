# An out-of-range BigInt element panics at the list-element coercion
# (to_fixed_check is range-checked), like the scalar Int32-assignment path.
from tpy import Int32


def too_big() -> int:
    return 10_000_000_000  # > Int32 max


def main() -> None:
    xs: list[Int32] = [too_big()]
    print(len(xs))


main()
