# A float value returned from a function declared -> float32 is refused, as
# an int64 returned from -> int32 is (docs/LANGUAGE_FEATURES.md, "Declared float slots", rule 2).
from tpy import float32


def narrow(wide: float) -> float32:
    return wide  # tpyc: error(/Type mismatch in return value: expected float32, got float/)


def main() -> None:
    print(narrow(0.1))


main()
