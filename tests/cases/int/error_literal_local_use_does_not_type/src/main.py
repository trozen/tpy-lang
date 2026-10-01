# A literal-seeded local takes its type from the values stored in it, never
# from a use: passing it to a uint64 parameter is a mismatch, and the
# diagnostic names the annotation that makes it one.
from tpy import uint64


def fu(x: uint64) -> None:
    pass


def main() -> None:
    a = 0
    # the use: `a` is the int32 its store gives, which a uint64 slot refuses
    fu(a)  # tpyc: error(/Type mismatch in argument 'x': expected uint64, got int32; 'a' takes its type from the values stored in it, not from its uses: annotate its first binding: a: uint64 = 0/)


main()
