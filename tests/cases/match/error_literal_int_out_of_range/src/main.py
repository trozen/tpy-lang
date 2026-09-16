# CPython RUNS this program (the arm is simply dead), so the rejection is a
# declared divergence: a fixed-width subject can never equal a literal outside
# its range, and an arm that can never fire is reported rather than emitted --
# the same category as the non-integral float arm
# (error_literal_float_never_matches). The label a `switch (int8_t)` would
# carry is what the toolchain refuses, so the verdict is sema's rather than a
# C++ diagnostic with no TPy location. A `bool` subject answers the same way
# ("holds 0 and 1"), and a folded float (`case 1e30:` over an `int32`) reaches
# this error under its own spelling. `int` (BigInt) is unbounded and never
# rejected -- see numeric_literal_kinds.
from tpy import int8


def classify(v: int8) -> str:
    match v:
        case 300:  # tpyc: error(/int literal pattern 300 can never match subject type 'int8', which holds -128\.\.127/)
            return "big"
        case _:
            return "other"


def main() -> None:
    pass


main()
