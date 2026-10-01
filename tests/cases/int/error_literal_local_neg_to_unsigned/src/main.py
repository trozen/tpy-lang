# A literal-seeded local holding a negative literal passed to an unsigned
# parameter is a plain mismatch: no annotation of it holds -1 as a uint64.
from tpy import uint64


def f(x: uint64) -> None:
    pass


def main() -> None:
    a = -1
    # the use; the annotation hint is withheld, as -1 fits no uint64
    f(a)  # tpyc: error(/Type mismatch in argument 'x': expected uint64, got int32/)


main()
