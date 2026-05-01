# When a literal-seeded local would have retro-widened to an unsigned target
# but a recorded literal value is negative, surface a range error that names
# the literal value -- not the bare "expected UInt64, got Int32" mismatch.
from tpy import UInt64


def f(x: UInt64) -> None:
    pass


def main() -> None:
    a = -1
    f(a)   # tpyc: error(/-1 assigned to 'a' is outside UInt64 range/)


main()
