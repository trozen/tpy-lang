# Test error: calling method on unbounded type parameter
from __future__ import annotations

def bad_call[T](item: T) -> str:
    return item.to_str()  # tpyc: error(/Cannot call method 'to_str' on type T/)
