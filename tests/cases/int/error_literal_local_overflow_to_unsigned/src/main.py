# Literal-seeded local with a recorded value that fits the default int type
# but does not fit a smaller unsigned target gets the range diagnostic at
# the call site, not a bare type-mismatch.
from tpy import UInt8


def f(x: UInt8) -> None:
    pass


def main() -> None:
    a = 300
    f(a)   # tpyc: error(/300 assigned to 'a' is outside UInt8 range/)


main()
