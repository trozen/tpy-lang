# A float32 field never takes a wider float implicitly, as an int32 field
# never takes an int64; an augmented store of a float result is refused like
# a plain one (docs/LANGUAGE_FEATURES.md, "Declared float slots", rule 2).
from tpy import float32


class Sample:
    v: float32

    def __init__(self) -> None:
        self.v = float32(1.5)

    def scale(self, wide: float) -> None:
        self.v *= wide  # tpyc: error(/Type mismatch in '\*=' to 'self.v': expected float32, got float/)


def main() -> None:
    s = Sample()
    s.scale(0.1)
    print(s.v)


main()
