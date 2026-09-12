# When a literal-seeded local would have retro-widened to an unsigned target
# but a recorded literal value is negative, surface a range error that names
# the literal value -- not the bare "expected uint64, got int32" mismatch.
from tpy import uint64


def f(x: uint64) -> None:
    pass


def main() -> None:
    a = -1
    f(a)   # tpyc: error(/-1 assigned to 'a' is outside uint64 range/)


main()
