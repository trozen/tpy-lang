from a import Pair
from tpy import Int32

def sum_pair(p: Pair) -> Int32:
    return p.x + p.y

# Constructs a's @dataclass record from the cycle peer using the
# auto-generated __init__. Exercises that the macro-emitted
# constructor is visible across the cycle, not just the field
# accessors.
def make_pair_sum(x: Int32, y: Int32) -> Int32:
    p = Pair(x, y)
    return sum_pair(p)
