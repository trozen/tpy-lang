# *unpack into a builtin that doesn't accept *args (print) is rejected
# cleanly by the analyze_expr safety net, rather than crashing with
# "Unknown expression type: TpyStarUnpack". Spreading into variadic
# builtins is a separate unimplemented feature (see TODO.md).
from tpy import Int32


def main() -> None:
    xs: list[Int32] = [1, 2, 3]
    print(*xs)  # tpyc: error(/Cannot use \*unpacking/)


main()
