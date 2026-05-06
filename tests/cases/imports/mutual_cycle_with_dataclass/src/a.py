from dataclasses import dataclass
from b import sum_pair
from tpy import Int32

@dataclass
class Pair:
    x: Int32
    y: Int32

# Real cycle edge: a calls into b's sum_pair, b takes a's Pair.
def add_pair(p: Pair) -> Int32:
    return sum_pair(p) + 1
