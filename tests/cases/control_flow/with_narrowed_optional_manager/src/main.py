# The `with` manager is a None-narrowed `T | None` name -- a bare pointer
# binding, so the manager borrows its DEREF. Sema rejects the un-narrowed
# spelling outright, so reaching codegen is the non-null proof.
from tpy import int32


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        print("counter exit", self.n)


class Slot:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    s: Slot

    def __init__(self, v: int32) -> None:
        self.s = Slot(v)

    def __enter__(self) -> Slot:
        return self.s

    def __exit__(self, et, ev, tb) -> None:
        print("holder exit", self.s.v)


def value_enter(c: Counter | None) -> int32:
    if c is None:
        return -1
    # Narrowed by the early return: the manager is the deref of `c`.
    with c as v:  # tpyc: ok
        return v + 1


def record_enter(h: Holder | None) -> int32:
    assert h is not None
    # The assert-narrowed spelling of the same row, with a record enter type:
    # the mutation through the borrow is visible on the manager afterwards.
    with h as slot:  # tpyc: ok
        slot.v += 7
    return h.s.v


def main() -> None:
    print(value_enter(Counter(4)))
    print(value_enter(None))
    print(record_enter(Holder(1)))


main()
