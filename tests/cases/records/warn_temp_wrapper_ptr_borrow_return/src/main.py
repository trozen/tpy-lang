# Why a TEMPORARY receiver cannot exempt a borrow-returning call at an owning
# slot: `Wrapper(...)` is a temporary, but `get()` hands back what its POINTER
# field aims at, which is the caller's `h.o` (or a global) and outlives the
# whole expression. The Own return therefore copies and warns at both
# spellings; the `copy()` twin says it explicitly and is silent. The copy is
# the ACKNOWLEDGED CPython divergence (CPython hands back the very Obj), so
# main prints only what both sides agree on.
from tpy import int32, Own, Ptr, copy, take_ptr


class Obj:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    o: Obj

    def __init__(self) -> None:
        self.o = Obj(1)


class Wrapper:
    p: Ptr[Obj]

    def __init__(self, p: Ptr[Obj]) -> None:
        self.p = p

    def get(self) -> Obj:
        # Borrows through the pointer field, not from the Wrapper.
        return self.p.__deref__()


G = Obj(7)


def take_field(h: Holder) -> Own[Obj]:
    # The receiver dies at end-of-statement; what it hands back does not.
    return Wrapper(take_ptr(h.o)).get()  # tpyc: warning(/copies Obj into owned storage/)


def take_global() -> Own[Obj]:
    return Wrapper(take_ptr(G)).get()  # tpyc: warning(/copies Obj into owned storage/)


def take_field_copy(h: Holder) -> Own[Obj]:
    return copy(Wrapper(take_ptr(h.o)).get())  # tpyc: ok


def main() -> None:
    h = Holder()
    print(take_field(h).n, take_global().n, take_field_copy(h).n)


main()
