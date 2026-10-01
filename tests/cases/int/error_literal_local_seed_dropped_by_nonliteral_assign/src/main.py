# A literal-seeded local takes the type its stores give (int32 here); a
# uint64 use does not retype it, and no annotation hint is offered, since
# the int32 value stored in it would not convert into a uint64.
from tpy import uint64, int32


def get_int() -> int32:
    return int32(7)


def f(x: uint64) -> None:
    pass


def main() -> None:
    a = 0
    a = get_int()   # non-literal write -- seed dropped
    f(a)            # tpyc: error(/Type mismatch in argument 'x': expected uint64, got int32/)


main()
