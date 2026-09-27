# A float local rebound to an `or` with an int operand is refused: the local's
# inferred type does not convert the int operand into a float.
from tpy import int32


def pick(a: int32) -> None:
    x = 0.5
    x = a or 2.5  # tpyc: error(/this `or` mixes int32 and float.*float\(a\) or 2\.5/)
    print(x)


pick(3)
