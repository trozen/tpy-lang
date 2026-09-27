# A declared `float | None` slot refuses a ternary of an int and a
# float-or-None arm (rejects valid, BUGS.md#declared-optional-float-select-int-arm).
from tpy import int32


def pick(a: int32, c: bool, opt: float | None) -> None:
    z: float | None = a if c else opt  # tpyc: error(/this conditional expression mixes int32 and float.*float\(a\) if c else opt/)
    print(z)


pick(3, True, None)
