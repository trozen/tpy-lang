# `if xs:` on an `Optional[list]`: the bare pointer test a record pointee
# takes would drop CPython's emptiness half, and sema attaches no mode for
# it, so the name keeps rejecting
# (BUGS.md#optional-container-truthiness-drops-emptiness).
from tpy import int32


def probe(xs: list[int32] | None) -> bool:
    if xs:  # tpyc: error(/stmt\.if:cond\.name/)
        return True
    return False


def main() -> None:
    print(probe([1]))


main()
