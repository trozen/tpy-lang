# A borrow-returning CALL at a list element slot copies into the element,
# the same as the lvalue spelling `xs.append(h.p)` already did. The copy
# still DIVERGES from CPython, which aliases -- so this case declares the
# copy via the warning and deliberately does not observe it (mutating the
# source and reading the container back would fail the cpy phase). The
# explicit spelling the warning names is pinned by list/insert_borrow_call_copy.
from tpy import int32


class Payload:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    p: Payload

    def __init__(self) -> None:
        self.p = Payload(42)

    def borrow(self) -> Payload:
        return self.p


def main() -> None:
    h = Holder()
    xs: list[Payload] = []
    # Both element inserts take the same Own[T] value slot, so both see the
    # borrow the call hands back.
    xs.append(h.borrow())  # tpyc: warning(/copies Payload into owned storage/)
    xs.insert(0, h.borrow())  # tpyc: warning(/copies Payload into owned storage/)
    print(len(xs), xs[0].v, xs[1].v)


main()
