# A `@dynamic` protocol value (lowered as `Base&`) coerces to `Ptr[Protocol]`
# by address-take. Mirrors the existing `record -> Ptr[Parent]` upcast path
# for the identity case (`P -> Ptr[P]`) -- needed when a function takes a
# protocol value as a param and stores its address (or forwards it to a
# `Ptr[P]` callee). Without this, sema rejects the address-take and the
# caller has to spell the outer param as `Ptr[P]` themselves.
from typing import Protocol
from tpy import Int32, Ptr, dynamic, nocopy, readonly


@dynamic
class Awaker(Protocol):
    def mark(self, task_id: Int32) -> None: ...


@nocopy
class Holder:
    awaker: Ptr[Awaker]

    def __init__(self, h: Awaker) -> None:
        # Identity: Awaker (value) -> Ptr[Awaker] via address-take.
        self.awaker = h  # tpyc: ok


def make_waker(h: Awaker, task_id: Int32) -> None:
    # Identity at a callee site: forwards a protocol value through Ptr[P].
    _consume(h, task_id)


def _consume(p: Ptr[Awaker], task_id: Int32) -> None:
    p.mark(task_id)


def read_only_take(h: Awaker) -> Ptr[readonly[Awaker]]:
    # Identity to readonly Ptr: address-of with const inner.
    p: Ptr[readonly[Awaker]] = h
    return p


class Executor(Awaker):
    log: list[Int32]
    def __init__(self) -> None:
        self.log = []
    def mark(self, task_id: Int32) -> None:
        self.log.append(task_id)


def main() -> None:
    e = Executor()
    # `e: Executor` -> `h: Awaker` at the callee (base-class binding),
    # then `h -> Ptr[Awaker]` inside via the new identity coercion.
    make_waker(e, 7)
    print(e.log[0])

    h = Holder(e)
    # h.awaker is a non-null Ptr[Awaker] pointing at e
    h.awaker.mark(99)
    print(e.log[1])

    # readonly Ptr return: just verify it doesn't crash on lvalue access
    rp = read_only_take(e)
    print(rp is not None)


main()
