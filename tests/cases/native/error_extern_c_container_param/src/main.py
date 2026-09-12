# A `list[T]` param stands for the whole sequence family (list / Span / Array):
# none of them has a C spelling, so the gate points at Ptr[T] plus an explicit
# length parameter instead.
from tpy.extern import export
from tpy import int32

@export(binding="C")
def total(xs: list[int32]) -> int32:  # tpyc: error(/parameter 'xs': type 'list\[int32\]' is not representable in the C ABI; use Ptr\[T\] plus an explicit length parameter/)
    n = 0
    for x in xs:
        n += x
    return n
