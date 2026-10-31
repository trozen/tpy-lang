# A Ptr[char] has no text rendering (C++ cannot tell a pointer to one char from
# a C string), so repr() refuses it with a diagnostic naming only Python types
# (docs/LANGUAGE_FEATURES.md "F-string Formatting"; f"{p}": error_fstring_ptr_char).
from tpy import Ptr, char, take_ptr


def show(p: Ptr[char]) -> str:
    return repr(p)  # tpyc: error(/No matching overload for 'repr' with argument types \(Ptr\[char\]\)/)


def main() -> None:
    c: char = "x"
    print(show(take_ptr(c)))


main()
