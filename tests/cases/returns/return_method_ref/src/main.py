# Test: local variable bound from method call gets a reference (not a copy) for record types
from tpy import readonly


class Inner:
    val: int

    def __init__(self, val: int) -> None:
        self.val = val

    def mutate(self) -> None:
        self.val += 10


class Holder:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner(1)

    def get(self) -> Inner:
        return self.inner

    def get_ro(self) -> readonly[Inner]:
        return self.inner


def test() -> None:
    h = Holder()

    # Mutable method: result should be Inner& (lvalue ref), not a copy
    x = h.get()
    x.mutate()
    print(h.inner.val)   # 11 (mutation visible through ref)

    # Readonly method: result should be const Inner& (const lvalue ref)
    y = h.get_ro()
    print(y.val)         # 11


test()
