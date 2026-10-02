# A float32 parameter keeps its declared type: an augmented assignment whose
# result is the wider float is refused, as an int64 result into an int32
# parameter is (docs/LANGUAGE_FEATURES.md, "Declared float slots", rule 2).
from tpy import float32


def scale(x: float32, wide: float) -> float32:
    x *= wide  # tpyc: error(/'x' is declared float32 and keeps its declared type, but 'x \*= wide' produces float; convert explicitly: x = float32\(x \* wide\)/)
    return x


def main() -> None:
    print(scale(float32(1.5), 0.1))


main()
