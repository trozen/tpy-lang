# INVERSE guard for the rebound-capture hoist: a capture that is NOT rebound
# must keep aliasing the matched object, so mutating through it is observed via
# the subject (CPython aliases too -- the capture names the same object). The
# hoist must fire only on rebinds, never widen to every capture.
from tpy import Own


class Inner:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n


class Holder:
    inner: Inner
    def __init__(self, inner: Inner) -> None:
        self.inner = inner


def make() -> Own[Holder]:
    return Holder(Inner(1))


def main() -> None:
    h = make()
    match h:
        case Holder(inner=q):
            q.n = 42       # mutate through the (non-rebound) aliasing capture
    print(h.inner.n)       # 42 -- proves the capture aliased, was not copied


main()
