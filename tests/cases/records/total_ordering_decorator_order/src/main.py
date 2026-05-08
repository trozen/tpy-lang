# @total_ordering composes with @dataclass regardless of decorator
# order. The macro defers its synthesis until all eager class macros
# have applied -- so by the time it picks an anchor and derives the
# missing comparisons, @dataclass has already added the synthesized
# __eq__. Both orderings should produce identical results, matching
# CPython's runtime late-binding behavior.
from dataclasses import dataclass
from functools import total_ordering
from tpy import Int32

@total_ordering
@dataclass
class Outer:
    rank: Int32
    def __lt__(self, other: "Outer") -> bool:
        return self.rank < other.rank

@dataclass
@total_ordering
class Inner:
    rank: Int32
    def __lt__(self, other: "Inner") -> bool:
        return self.rank < other.rank

a, b = Outer(Int32(1)), Outer(Int32(2))
print(a < b, a <= b, a > b, a >= b, a == Outer(Int32(1)))

c, d = Inner(Int32(1)), Inner(Int32(2))
print(c < d, c <= d, c > d, c >= d, c == Inner(Int32(1)))
