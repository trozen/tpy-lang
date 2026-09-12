# Regression: an out-of-range read through a narrowed pointer-repr
# `Optional[list]` receiver must raise IndexError. It used to render a raw
# `(*lst)[i]`, which silently read past the end instead of panicking.
from tpy import int32


def read_oob(lst: list[int32] | None) -> None:
    if lst is None:
        return
    print("before")
    print(lst[10])          # the subject: bounds check must fire


def main() -> None:
    read_oob([1, 2, 3])


main()
