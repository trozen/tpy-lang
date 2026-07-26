# A REFERENCE-typed capture reused by a nested match re-seats the binding (it
# aliases the inner subject) rather than copying it or writing through to the
# outer one. `reseats` and `as_pattern` mutate the aliased object after the
# re-seat and read back through the capture, so a silent copy shows up as a
# wrong value instead of passing parity-blind; `outer_untouched` is the other
# half (the write-through that must NOT happen), and `mixed_const` pins the
# const-ness of the shared slot.
class Inner:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n


class Holder:
    inner: Inner
    def __init__(self, inner: Inner) -> None:
        self.inner = inner


def reseats(h: Holder, g: Holder) -> int:
    match h:
        case Holder(inner=q):
            match g:
                case Holder(inner=q):   # tpyc: ok
                    pass
            g.inner.n = 99              # mutate what q now aliases
            return q.n                  # 99 if aliased; 2 if copied
    return -1


def outer_untouched(h: Holder, g: Holder) -> int:
    # The re-seat must not write THROUGH the outer binding: h keeps its own
    # object (CPython rebinds the local, leaving the matched object alone).
    match h:
        case Holder(inner=q):
            match g:
                case Holder(inner=q):
                    pass
    return h.inner.n


def mixed_const(h: Holder, g: Holder) -> int:
    # The outer subject is mutated (so it binds mutably) while the inner one is
    # only read (so it binds const). Both re-seat one shared slot, so the slot
    # must take the const form for either bind to compile.
    match h:
        case Holder(inner=q):
            h.inner.n = 5
            match g:
                case Holder(inner=q):
                    pass
            return q.n
    return -1


def as_pattern(h: Holder, g: Holder) -> int:
    # `case ... as name` binds through the same capture machinery, and binds
    # the WHOLE object -- mutate through the re-seated target to prove it
    # aliased `g` rather than copying it.
    match h:
        case Holder() as w:
            match g:
                case Holder() as w:     # tpyc: ok
                    pass
            g.inner.n = 55
            return w.inner.n            # 55 if aliased; 2 if copied
    return -1


def main() -> None:
    print(reseats(Holder(Inner(1)), Holder(Inner(2))))          # 99
    print(outer_untouched(Holder(Inner(1)), Holder(Inner(2))))   # 1
    print(mixed_const(Holder(Inner(1)), Holder(Inner(2))))       # 2
    print(as_pattern(Holder(Inner(1)), Holder(Inner(2))))        # 55


main()
