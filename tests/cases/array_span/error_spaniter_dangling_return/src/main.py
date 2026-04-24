# SpanIter[T] return referencing a local span/array dangles after the function
# returns. Previously missed for the same reason as Span.
from tpy import Int32, Array, Span, SpanIter

def bad_spaniter() -> SpanIter[Int32]:
    arr: Array[Int32, 3] = [1, 2, 3]
    s: Span[Int32] = arr
    it: SpanIter[Int32] = SpanIter(s)
    return it  # tpyc: error(/Cannot return SpanIter referencing a local or temporary/)
