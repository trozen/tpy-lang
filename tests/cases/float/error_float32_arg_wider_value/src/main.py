# A float value at a float32 parameter is refused at the call, as an int64 at
# an int32 parameter is; the caller spells float32(...) to narrow (docs/LANGUAGE_FEATURES.md, "Declared float slots", rule 2).
from tpy import float32


def show(x: float32) -> None:
    print(x)


def main(wide: float) -> None:
    show(wide)  # tpyc: error(/Type mismatch in argument 'x': expected float32, got float/)


main(0.1)
