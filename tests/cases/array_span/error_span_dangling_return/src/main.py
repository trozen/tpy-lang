# Span[T] return referencing a local array dangles after the function returns.
# Previously missed: check_dangling_reference had an early exit for all
# value types, which accepted Span returns borrowing from locals.
from tpy import int32, Array, Span

def bad_span_local_array() -> Span[int32]:
    arr: Array[int32, 3] = [1, 2, 3]
    s: Span[int32] = arr
    return s  # tpyc: error(/Cannot return Span referencing a local or temporary/)
