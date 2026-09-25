# A tuple ternary whose arms differ in an integer VARIABLE element is refused
# (BUGS.md#literal-elem-tuple-ternary-mismatch).
from tpy import int32, int64


def pick(f: bool, x: int64) -> tuple[int64, str]:
    t: tuple[int32, str] = (3, "c")
    # The int32 element is not widened against the int64 one.
    return t if f else (x, "d")  # tpyc: error(/'tuple\[int32, str\]' and 'tuple\[int64, str\]'/)


def main() -> None:
    print(pick(True, 5))


main()
