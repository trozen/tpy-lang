from dataclasses import dataclass
from b import sum_pair
from tpy import int32

@dataclass
class Pair:
    x: int32
    y: int32

# Real cycle edge: a calls into b's sum_pair, b takes a's Pair.
def add_pair(p: Pair) -> int32:
    return sum_pair(p) + 1
