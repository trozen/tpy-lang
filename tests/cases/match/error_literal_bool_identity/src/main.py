# CPython RUNS this program (the wildcard fires), so the rejection is a
# stricter-by-design rule, not a gap: PEP 634 compares a True/False literal
# pattern by IDENTITY, not `==`, so `case True:` can only ever match a `bool`
# subject, and emitting `== true` here would match where CPython does not.
# The rule is recorded in docs/LANGUAGE_FEATURES.md, "Control Flow" -> "Other",
# the `match`/`case` bullet "Numeric literal patterns are cross-kind".
# The `==` family -- an int or float literal at any numeric slot -- is the
# happy case numeric_literal_kinds.
from tpy import int32


def classify(n: int32) -> str:
    match n:
        case True:  # tpyc: error(/bool literal pattern can never match/)
            return "one"
        case _:
            return "other"


def main() -> None:
    pass


main()
