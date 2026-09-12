# Reassignment to a Literal[...]-annotated local stays narrowed when every
# assigned value is within the declared set, including across branch joins.
# Regression guard for the out-of-set reassignment check landed in the same
# patch.
from typing import Literal, overload
from tpy import int32


@overload
def pick(v: Literal["r", "w"]) -> int32: ...
@overload
def pick(v: str) -> int32: ...
def pick(v: str) -> int32:
    return int32(99)


def main() -> None:
    m: Literal["r", "w"] = "r"
    print(pick(m))   # Literal overload
    m = "w"          # in-set reassignment
    print(pick(m))   # still Literal overload

    # Branch join: both arms stay in the declared set, dispatch unaffected.
    cond: bool = True
    n: Literal["r", "w"] = "r"
    if cond:
        n = "w"
    print(pick(n))   # Literal overload after join


main()
