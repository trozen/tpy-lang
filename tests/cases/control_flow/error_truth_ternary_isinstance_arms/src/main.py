# A truth-tested ternary with int/float arms under an isinstance test has no
# lowering yet (BUGS.md#truth-ternary-isinstance-arms).
from tpy import int32


def main(a: int32, g: float, u: int32 | str) -> None:
    # Each arm would be tested on its own, under the isinstance narrowing.
    if a if isinstance(u, int32) else g:  # tpyc: error(/ifexpr\.truth_arms_isinstance/)
        print("isin")


main(3, 2.5, 1)
