# A readonly Rc/Weak handle can clone/downgrade/upgrade -- the refcount bump
# is interior (outside the readonly boundary) and yields a readonly-payload
# handle, so readonly can't be laundered into mutable T.
from tpy import int32, Own, readonly
from tplib import Rc
from tplib.rc import Weak


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def observe(r: readonly[Rc[Counter]]) -> int32:
    shared = r.clone()  # tpyc: type(/readonly\[Counter\]/)
    return shared.get().n


def via_readonly_weak(w: readonly[Weak[Counter]]) -> int32:
    w2 = w.clone()      # Weak.clone on a readonly receiver
    a2 = w2.upgrade()
    b2 = w.upgrade()    # Weak.upgrade on a readonly receiver
    if a2 is None or b2 is None:
        return -1
    return a2.get().n


def downgrade_readonly(r: readonly[Rc[Counter]]) -> int32:
    w = r.downgrade()
    up = w.upgrade()
    return up.get().n if up is not None else -1


class Registry:
    _shared: Rc[Counter]

    def __init__(self, c: Own[Rc[Counter]]) -> None:
        self._shared = c

    @readonly
    def handle(self) -> Own[Rc[readonly[Counter]]]:
        return self._shared.clone()


def main() -> None:
    a = Rc.new(Counter(1))
    b = a.clone()
    b.get().n = 42
    print(observe(a))
    print(downgrade_readonly(a))

    wk = a.downgrade()
    print(via_readonly_weak(wk))

    reg = Registry(a.clone())
    print(reg.handle().get().n)


main()
