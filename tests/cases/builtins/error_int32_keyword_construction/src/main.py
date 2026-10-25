# The fixed-width int constructors take their value positionally only, as
# int() does in CPython (sibling: error_int_keyword_construction).
from tpy import int32


def main() -> None:
    print(int32(x=5))  # tpyc: error(/No matching overload for 'int32' with argument types \(x=int\)/)


main()
