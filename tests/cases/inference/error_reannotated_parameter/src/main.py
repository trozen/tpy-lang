# A parameter is bound on entry, so an annotation on it in the body is a later
# annotation and is refused like one on a local (mypy: Name "p" already
# defined). (A documented restriction: docs/LANGUAGE_FEATURES.md, "Numeric
# widening across reassignments".)
from tpy import int8, int16


def main(p: int8) -> None:
    # the parameter re-annotated with a wider type
    p: int16 = 3  # tpyc: error(/'p' is already bound at line 8; an annotation goes on the first binding/)
    print(p)


main(1)
