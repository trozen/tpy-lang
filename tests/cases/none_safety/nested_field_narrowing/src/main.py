# Test narrowing optional fields through multi-level field access (obj.a.b)
from typing import Optional

class Inner:
    value: Optional[int]
    def __init__(self, value: Optional[int]) -> None:
        self.value = value

class Outer:
    inner: Inner
    def __init__(self, inner: Inner) -> None:
        self.inner = inner

def get_value(o: Outer) -> int:
    if o.inner.value is not None:
        return o.inner.value  # tpyc: ok
    return 0

def get_value_truthy(o: Outer) -> int:
    if o.inner.value:
        return o.inner.value  # tpyc: ok
    return 0

def main() -> None:
    o1 = Outer(Inner(42))
    o2 = Outer(Inner(None))
    print(get_value(o1))
    print(get_value(o2))
    print(get_value_truthy(o1))
    print(get_value_truthy(o2))

main()
