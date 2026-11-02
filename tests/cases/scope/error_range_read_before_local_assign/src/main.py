# A `for` over `range(3)` in a function that later assigns `range`: the head
# reads the function's local `range` before it is bound (CPython's
# UnboundLocalError), never the builtin -- the range special-casing does not
# take it as the builtin either.
from tpy import int32


def main() -> None:
    # The builtin the later `range = 0` makes local.
    for i in range(3):  # tpyc: error(/'range' is read before it is bound on line 12: .* does not see the builtin 'range'/)
        print(i)
    range = 0
    print(range)


main()
