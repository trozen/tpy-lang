# CPython RUNS this program (the arm is simply dead), so the rejection is a
# declared divergence: an arm that can never fire is reported rather than
# emitted -- the same category as the bool-identity arm. A whole-number
# subject can never equal a non-integral float (this case); it can never equal
# a value outside its own range either, whichever kind spells it
# (error_literal_int_out_of_range). See
# docs/LANGUAGE_FEATURES.md, "Control Flow" -> "Other", the `match`/`case`
# bullet "Numeric literal patterns are cross-kind". The integral spelling
# (`case 2.0:`) is warned and folded to `case 2:` instead -- see
# numeric_literal_kinds.
from tpy import int32


def classify(v: int32) -> str:
    match v:
        case 2.0:  # tpyc: warning(/spell it 2/)
            return "two"
        case 1.5:  # tpyc: error(/float literal pattern 1\.5 can never match subject type 'int32'/)
            return "never"
        case _:
            return "other"


def main() -> None:
    pass


main()
