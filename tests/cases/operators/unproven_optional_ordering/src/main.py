# An UNPROVEN value-Optional on the left of a mixed-sign ordering comparison:
# the operand unwraps through its runtime check and `::std::cmp_*` compares
# that inner value. The line also draws the optional-access and mixed-sign
# warnings.
from tpy import int32, uint32


def f(a: int32 | None, b: uint32) -> bool:
    # `a` is never proven non-None before the ordering test.
    return a < b  # tpyc: warning(/Potential None access/) warning(/comparison between signed and unsigned/)


def main() -> None:
    print(f(3, 5), f(-1, 5))


main()
