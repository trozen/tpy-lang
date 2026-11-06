# A walrus rebinding a bound name is its `=` statement, then a read of the
# name, for every form of the binding and at every position: the record,
# Optional, union and owning-`for` forms, the global and module-level
# sections here, the rest in the `part_*` modules.
from tpy import int32
from fixtures import A, B, H, Ring, bump, show, steps
import part_frames
import part_positions
import part_sinks_returns
import part_truthy_cond
import part_tuples
import part_values


calls = 0


def nxt() -> int32:
    global calls
    calls += 1
    return calls


def field(h: H) -> None:
    x = h.a
    # The walrus re-points the record local at the field.
    if (x := h.b).n > 0:  # tpyc: ok
        x.n += 10
    print("field", h.a.n, h.b.n)


def method_recv(h: H) -> None:
    x = h.a
    # A method called on the rebinding walrus reaches the new object.
    print("method_recv", (x := h.b).bump())  # tpyc: ok
    x.n += 1
    print("method_recv", h.a.n, h.b.n)


def method_recv_ptr(h: H) -> None:
    y = h.a
    y = h.b
    x = h.a
    # The walrus's value is a rebound (pointer) local's object.
    print("method_recv_ptr", (x := y).bump())  # tpyc: ok
    x.n += 1
    print("method_recv_ptr", h.a.n, h.b.n)


def element(h: H) -> None:
    x = h.a
    # Rebound to a list element.
    print("element", (x := h.xs[1]).n)  # tpyc: ok
    x.n += 10
    print("element", h.a.n, h.xs[1].n)


def borrow_call(h: H) -> None:
    x = h.a
    # Rebound to what a borrowing method returns.
    print("borrow_call", (x := h.pick()).n)  # tpyc: ok
    x.n += 10
    print("borrow_call", h.a.n, h.b.n)


def param(h: H, p: A) -> None:
    x = h.a
    # Rebound to a parameter.
    print("param", (x := p).n)  # tpyc: ok
    x.n += 10
    print("param", h.a.n, p.n)


def fresh(h: H) -> None:
    x = h.a
    # Rebound to a fresh object, which gets storage of its own.
    print("fresh", (x := A(7)).n)  # tpyc: ok
    x.n += 10
    print("fresh", h.a.n, x.n)


def fresh_local() -> None:
    f = A(1)
    g = f
    # Rebinding a local that owns its first object keeps that object for `g`.
    if (f := A(2)).n > 0:  # tpyc: ok
        f.n += 10
    g.n += 100
    print("fresh_local", g.n, f.n)


def opt_field(h: H) -> None:
    x = h.o
    # A pointer-repr Optional local rebound to a record field.
    if (x := h.b) is not None:  # tpyc: ok
        x.n += 10
    print("opt_field", h.a.n, h.b.n)


def opt_opt(h: H) -> None:
    x: A | None = None
    # Rebound to an Optional field.
    if (x := h.o) is not None:  # tpyc: ok
        x.n += 10
    print("opt_opt", h.b.n, h.o.n if h.o is not None else -1)


def opt_none(h: H) -> None:
    x = h.o
    # Rebound to None.
    print("opt_none", (x := None) is None)  # tpyc: ok
    print("opt_none", x is None)


def opt_narrowed(h: H, p: A | None) -> None:
    x = h.o
    if p is not None:
        # Rebound to a narrowed Optional parameter.
        if (x := p) is not None:  # tpyc: ok
            x.n += 10
    print("opt_narrowed", h.a.n, p.n if p is not None else -1)


def while_cond(r: Ring) -> None:
    x = r.head
    total = 0
    if x is not None:
        x = r.items[0]
    # The rebinding walrus as a `while` condition.
    while x is not None and (x := r.after(x)) is not None:  # tpyc: ok
        x.n += 100
        total += x.n
    print("while_cond", total, r.items[2].n, r.items[1].n)


def union(h: H, p: A | B) -> None:
    x = p
    # A pointer-variant union local rebound to each alternative.
    print("union", show((x := h.bb)))  # tpyc: ok
    bump(x)
    print("union", show((x := h.a)))  # tpyc: ok
    bump(x)
    print("union", h.bb.s, h.a.n)


def own_for(h: H) -> None:
    for x in steps():
        y = x
        print("own_for", y.n)
        # An owning `for` target rebound to a field.
        print("own_for", (x := h.a).n)  # tpyc: ok
        x.n += 10
        y.n += 100
        print("own_for", y.n, x.n)
    print("own_for", h.a.n)


def own_for_fresh() -> None:
    for s in steps():
        k = s
        # An owning `for` target rebound to a fresh object.
        if (s := A(2)).n > 0:  # tpyc: ok
            s.n += 10
        print("own_for_fresh", k.n, s.n)


def once() -> None:
    x = 0
    xs = [0, 0, 0, 0]
    i = 0
    # Each walrus is evaluated once, whatever its operator.
    b = (x := nxt()) in (1, 2, 3)  # tpyc: ok
    print("once", "in", b, x, calls)
    v = (x := nxt()) or 7  # tpyc: ok
    print("once", "or", v, x, calls)
    c = 0 < (x := nxt()) < 5  # tpyc: ok
    print("once", "chain", c, x, calls)
    xs[(i := i + 1)] += 100  # tpyc: ok
    print("once", "aug", xs, i)


counter = 0


def global_name() -> None:
    global counter
    # A `global` rebound from a function.
    print("global", (counter := counter + 1))  # tpyc: ok
    print("global", counter)


def main() -> None:
    field(H())
    method_recv(H())
    method_recv_ptr(H())
    element(H())
    borrow_call(H())
    h = H()
    param(h, h.b)
    fresh(H())
    fresh_local()
    opt_field(H())
    opt_opt(H())
    opt_none(H())
    h = H()
    opt_narrowed(h, h.b)
    while_cond(Ring())
    h = H()
    union(h, h.a)
    own_for(H())
    own_for_fresh()
    global_name()
    part_values.main()
    part_positions.main()
    part_frames.main()
    part_sinks_returns.main()
    part_truthy_cond.main()
    part_tuples.main()
    once()


main()
mh = H()
mx = mh.a
# Module level.
print("module", (mx := mh.b).n)  # tpyc: ok
mx.n += 10
print("module", mh.a.n, mh.b.n)
