# @total_ordering composes with @dataclass regardless of decorator
# order. The macro defers its synthesis until all eager class macros
# have applied -- so by the time it picks an anchor and derives the
# missing comparisons, @dataclass has already added the synthesized
# __eq__. Both orderings should produce identical results, matching
# CPython's runtime late-binding behavior.
from dataclasses import dataclass
from functools import total_ordering
from tpy import int32

@total_ordering
@dataclass
class Outer:
    rank: int32
    def __lt__(self, other: "Outer") -> bool:
        return self.rank < other.rank

@dataclass
@total_ordering
class Inner:
    rank: int32
    def __lt__(self, other: "Inner") -> bool:
        return self.rank < other.rank

a, b = Outer(int32(1)), Outer(int32(2))
print(a < b, a <= b, a > b, a >= b, a == Outer(int32(1)))

c, d = Inner(int32(1)), Inner(int32(2))
print(c < d, c <= d, c > d, c >= d, c == Inner(int32(1)))
