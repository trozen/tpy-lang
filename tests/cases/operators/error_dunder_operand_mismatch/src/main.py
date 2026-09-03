# An operand the comparison dunder's slot would NARROW keeps rejecting: C++
# converts the float to `Int32` silently, printing a value CPython disagrees
# with (BUGS.md#derived-ne-unchecked-operand).
from tpy import Int32


class Count:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: Int32) -> bool:
        return self.n == other


def main() -> None:
    c = Count(2)
    # The explicit `c.__eq__(2.5)` is already a sema error; the OPERATOR
    # spelling must not admit what the call refuses.
    print(c == 2.5)  # tpyc: error(/binop\.shape\.==\.record/)


main()
