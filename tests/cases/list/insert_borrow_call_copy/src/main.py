# The explicit spelling at the element slot warn_insert_borrow_call_copy
# reports implicitly: `xs.append(copy(h.borrow()))` is warning-free for a
# RECORD and for a CONTAINER payload alike. COPY SEMANTICS ARE THE POINT --
# each insert is observed by mutating the source afterwards and reading the
# element back, and CPython agrees because `copy()` deep-copies there.
from tpy import Int32, copy


class Payload:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Holder:
    p: Payload
    items: list[Int32]

    def __init__(self) -> None:
        self.p = Payload(42)
        self.items = [1, 2]

    def brec(self) -> Payload:
        return self.p

    def bctr(self) -> list[Int32]:
        return self.items


def main() -> None:
    h = Holder()
    recs: list[Payload] = []
    # The record payload: the borrow-returning call is copied in one step.
    recs.append(copy(h.brec()))  # tpyc: ok
    recs.insert(0, copy(h.p))  # tpyc: ok

    ctrs: list[list[Int32]] = []
    # The container payload takes the same one-step spelling.
    ctrs.append(copy(h.bctr()))  # tpyc: ok
    ctrs.insert(0, copy(h.items))  # tpyc: ok

    # Mutating the sources leaves every inserted copy alone.
    h.p.v = 7
    h.items.append(3)
    print(h.p.v, recs[0].v, recs[1].v)
    print(len(h.items), len(ctrs[0]), len(ctrs[1]))


main()
