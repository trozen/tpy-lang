# A REFERENCE-typed capture reused by a nested match re-seats the binding (it
# aliases the inner subject) rather than copying it or writing through to the
# outer one. `reseats_mutable` and `as_pattern_mutable` mutate the aliased
# object after the re-seat and read back through the capture, so a silent
# copy shows up as a wrong value instead of passing parity-blind;
# `outer_untouched` is the other half (the write-through that must NOT
# happen).
class Inner:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n


class Holder:
    inner: Inner
    def __init__(self, inner: Inner) -> None:
        self.inner = inner


def outer_untouched(h: Holder, g: Holder) -> int:
    # The re-seat must not write THROUGH the outer binding: h keeps its own
    # object (CPython rebinds the local, leaving the matched object alone).
    match h:
        case Holder(inner=q):
            match g:
                case Holder(inner=q):
                    pass
    return h.inner.n


def reseats_mutable(h: Holder, g: Holder) -> int:
    # Both subjects are written, so every source of the shared slot is
    # mutable (a read-only one beside them rejects).
    match h:
        case Holder(inner=q):
            h.inner.n = 5
            match g:
                case Holder(inner=q):   # tpyc: ok
                    pass
            g.inner.n = 99              # mutate what q now aliases
            return q.n                  # 99 if aliased; 2 if copied
    return -1


def as_pattern_mutable(h: Holder, g: Holder) -> int:
    # `case ... as name` binds the WHOLE object through the same machinery;
    # both subjects are written, and a write after the re-seat shows the
    # target aliases `g`.
    match h:
        case Holder() as w:
            h.inner.n = 5
            match g:
                case Holder() as w:     # tpyc: ok
                    pass
            g.inner.n = 55
            w.inner.n += 1
            return w.inner.n            # 56 if aliased
    return -1


def main() -> None:
    print("outer_untouched:",
          outer_untouched(Holder(Inner(1)), Holder(Inner(2))))  # 1
    print("reseats_mutable:",
          reseats_mutable(Holder(Inner(1)), Holder(Inner(2))))  # 99
    h = Holder(Inner(1))
    g = Holder(Inner(2))
    print("as_pattern_mutable:", as_pattern_mutable(h, g), h.inner.n,
          g.inner.n)


main()
