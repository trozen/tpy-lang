# SpanIter[T] return referencing a local span/array dangles after the function
# returns. Previously missed for the same reason as Span.
from tpy import int32, Array, Span, SpanIter

def bad_spaniter() -> SpanIter[int32]:
    arr: Array[int32, 3] = [1, 2, 3]
    s: Span[int32] = arr
    it: SpanIter[int32] = SpanIter(s)
    return it  # tpyc: error(/Cannot return SpanIter referencing a local or temporary/)
