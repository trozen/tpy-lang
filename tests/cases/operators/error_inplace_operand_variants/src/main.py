# An in-place operand no `__iadd__` variant takes names every variant's
# parameter in the mismatch.
from tpy import dispatch


class VI:
    def __init__(self) -> None:
        pass

    @dispatch
    def __iadd__(self, x: int) -> "VI":
        return self

    @dispatch
    def __iadd__(self, x: float) -> "VI":
        return self


def main() -> None:
    vi = VI()
    vi += "s"  # tpyc: error(/Type mismatch in '\+=' operand: expected int or float, got str/)


main()
