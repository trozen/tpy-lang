# A folded constant at an annotated comprehension element is range-checked
# against the element type, as the hand literal and a tuple element are: a
# literal must fit its fixed-width slot (docs/LANGUAGE_FEATURES.md, "Mixed
# Arithmetic and Type Promotion" `uint8(-3)` and "Value-seeded").
from tpy import int32


def main() -> None:
    xs: list[int32] = [2**40 for _ in range(2)]  # tpyc: error(/Integer literal 1099511627776 is outside int32 range .* in list comprehension element/)
    print(xs)


main()
