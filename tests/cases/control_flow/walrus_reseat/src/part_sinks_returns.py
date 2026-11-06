# The rebinding walrus at owning sinks and returns: arguments, `Own`
# results, returns under a mutating `finally`.
from tpy import Own

from fixtures import A, H, N, W


def arg_sink() -> None:
    x = A(1)
    l: list[A] = []
    # An owning argument copies the object the walrus binds while `x` still
    # reads it, as `x = A(2); l.append(x)` does, and says so.
    l.append((x := A(2)))  # tpyc: warning(/copies A into owned storage/)
    print("arg_sink", l[0].n, x.n)
    w = W((x := A(3)))  # tpyc: warning(/copies A into owned storage/)
    print("arg_sink", w.a.n, x.n)
    # The walrus's value is `x`'s last use: it moves in.
    l.append((x := A(5)))  # tpyc: ok
    l[1].n += 10
    print("arg_sink", len(l), l[1].n)


def own_return() -> Own[A]:
    x = A(1)
    keep = x
    keep.n += 100
    print("own_return", keep.n, x.n)
    # Returned at its last use, the rebound object moves out, as for
    # `x = A(3); return x`.
    return (x := A(3))  # tpyc: ok


def borrowed_return(h: H) -> Own[A]:
    x = A(0)
    print("borrowed_sink", x.n)
    # A borrowed source at an `Own` result copies, as `x = h.a; return x`
    # does, and says so (the copy is the warned divergence).
    return (x := h.a)  # tpyc: warning(/copies A into owned storage/)


def borrowed_sink(h: H) -> None:
    x = A(0)
    l: list[A] = []
    # A borrowed source at an owning argument copies, as `x = h.a;
    # l.append(x)` does.
    l.append((x := h.a))  # tpyc: warning(/copies A into owned storage/)
    print("borrowed_sink", l[0].n, x.n)
    r = borrowed_return(h)
    print("borrowed_sink", r.n)


def finally_return() -> Own[A]:
    b = A(1)
    keep = b
    try:
        # The returned object is the walrus's, mutated by the `finally`.
        return (b := A(2))  # tpyc: ok
    finally:
        b.n += 90
        keep.n += 1000


def finally_return_nocopy() -> Own[N]:
    b = N(1)
    try:
        # A non-copyable result is deferred too.
        return (b := N(2))  # tpyc: ok
    finally:
        b.n = 99


def own_return_nocopy() -> Own[N]:
    x = N(1)
    print("own_return_nocopy", x.n)
    # A non-copyable object moves out too.
    return (x := N(2))  # tpyc: ok


def main() -> None:
    arg_sink()
    a = own_return()
    print("own_return", a.n)
    n = own_return_nocopy()
    print("own_return_nocopy", n.n)
    borrowed_sink(H())
    fa = finally_return()
    print("finally_return", fa.n)
    fn = finally_return_nocopy()
    print("finally_return_nocopy", fn.n)
