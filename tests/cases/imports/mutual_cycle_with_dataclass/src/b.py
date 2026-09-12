from a import Pair
from tpy import int32

def sum_pair(p: Pair) -> int32:
    return p.x + p.y

# Constructs a's @dataclass record from the cycle peer using the
# auto-generated __init__. Exercises that the macro-emitted
# constructor is visible across the cycle, not just the field
# accessors.
def make_pair_sum(x: int32, y: int32) -> int32:
    p = Pair(x, y)
    return sum_pair(p)
