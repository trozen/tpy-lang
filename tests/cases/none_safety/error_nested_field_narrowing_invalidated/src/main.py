# Test that writing an intermediate field invalidates deeper narrowing facts
from typing import Optional

class Inner:
    value: Optional[int]
    def __init__(self, value: Optional[int]) -> None:
        self.value = value

class Outer:
    inner: Inner
    def __init__(self, inner: Inner) -> None:
        self.inner = inner

def test(o: Outer) -> int:
    if o.inner.value is not None:
        o.inner = Inner(None)
        x: int = o.inner.value  # tpyc: error(/Type mismatch/)
    return 0
