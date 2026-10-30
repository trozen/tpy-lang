# Pins the restriction in docs/LANGUAGE_FEATURES.md "F-string Formatting"
# (Optional and Ptr): a Ptr[char] has no text rendering -- C++ cannot tell a
# pointer to one char from a C string -- so an f-string refuses it.
from tpy import Ptr, char, take_ptr


def show(p: Ptr[char]) -> str:
    return f"{p}"  # tpyc: error(/Type Ptr\[char\] cannot be used in f-string/)


def main() -> None:
    c: char = "x"
    print(show(take_ptr(c)))


main()
