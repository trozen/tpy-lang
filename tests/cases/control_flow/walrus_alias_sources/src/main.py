# Walrus borrow-alias source shapes: a pointer-slot GLOBAL, a record FIELD, a
# container FIELD, an Optional container ELEMENT, and a readonly-inferred param
# as the receiver. Every one aliases its source, so a mutation on either side
# has to be visible through the other.
class Rec:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def get(self) -> int:
        return self.n


class Holder:
    inner: Rec
    kid: list[int]

    def __init__(self) -> None:
        self.inner = Rec(1)
        self.kid = [1]


G = Rec(3)


def from_global() -> int:
    if (q := G).n > 0:              # tpyc: ok -- a pointer-slot global source
        q.n += 10                   # writes through to the global
        return q.n
    return 0


def from_field(h: Holder) -> int:
    if (q := h.inner).n > 0:        # tpyc: ok -- a record FIELD source
        q.n += 10
        return q.n
    return 0


def from_container_field(h: Holder) -> int:
    if len(row := h.kid) > 0:       # tpyc: ok -- a container FIELD source
        h.kid.append(99)            # mutating the SOURCE shows through `row`
        return len(row)
    return 0


def from_optional_elem(xs: list[Rec | None]) -> int:
    if (r := xs[0]) is not None:    # tpyc: ok -- an Optional ELEMENT source
        r.n += 5
        return r.n
    return 0


def readonly_recv(x: Rec) -> int:
    # `x` is only read, so it is inferred readonly -- the alias must be `const`.
    return (p := x).get()           # tpyc: ok


def main() -> None:
    print(from_global(), G.n)
    h = Holder()
    print(from_field(h), h.inner.n)
    print(from_container_field(h), len(h.kid))
    xs: list[Rec | None] = [Rec(3)]
    print(from_optional_elem(xs))
    first = xs[0]
    if first is not None:
        print(first.n)
    print(readonly_recv(Rec(7)))


main()
