# A float local rebound to a ternary with an int arm is refused: the local's
# inferred type hints the arms but does not convert the int one into a float.
from tpy import int32


def pick(a: int32, c: bool) -> None:
    x = 0.5
    x = a if c else 2.5  # tpyc: error(/this conditional expression mixes int32 and float.*float\(a\) if c else 2\.5/)
    print(x)


pick(3, True)
