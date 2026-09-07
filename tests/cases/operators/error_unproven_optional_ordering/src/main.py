# An UNPROVEN value-Optional on the left of an ordering comparison: the
# operand unwraps through a runtime check, which the mixed-sign comparison
# row cannot mirror, so `a < b` (`a: Int32 | None`) is rejected today. The
# same line also draws the optional-access and mixed-sign warnings.
from tpy import Int32, UInt32


def f(a: Int32 | None, b: UInt32) -> bool:
    # `a` is never proven non-None before the ordering test.
    return a < b  # tpyc: error(/in\ function\ 'f':\ this\ construct\ is/)


def main() -> None:
    print(f(3, 5))


main()
