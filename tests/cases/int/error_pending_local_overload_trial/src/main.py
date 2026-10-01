# A conversion over a pending local deferred before an overloaded call is
# resolved after the call, so its refusal sees every store of the function:
# no annotation is offered for `p`, whose later int32 store an int8 would
# refuse. The winning candidate's trial of the lambda settles `p` first
# (docs/LANGUAGE_FEATURES.md "Numeric widening across reassignments", current
# limitations).
from tpy import int8, int32, Fn, dispatch


def w32(v: int32) -> int32:
    return v


@dispatch
def apply_one[T, U](f: Fn[[int32], U], x: T) -> int32:
    return 1


@dispatch
def apply_one[T, U](f: Fn[[str], U], x: T) -> int32:
    return 1


def main() -> None:
    p = 1
    t: int8 = 7
    t = p  # tpyc: error(/Type mismatch in reassignment to 't': expected int8, got int32/)
    print(apply_one(lambda a: a + p, 0), t)
    p = w32(2)
    print(p)


main()
