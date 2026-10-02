# *unpack into a builtin that doesn't accept *args (str) is rejected
# cleanly by the analyze_expr safety net, rather than crashing with
# "Unknown expression type: TpyStarUnpack". Spreading into variadic
# builtins other than print is a separate unimplemented feature (see TODO.md).
from tpy import int32


def main() -> None:
    xs: list[int32] = [1, 2, 3]
    print(str(*xs))  # tpyc: error(/Cannot use \*unpacking/)


main()
