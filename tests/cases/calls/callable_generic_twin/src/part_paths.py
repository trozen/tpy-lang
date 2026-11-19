# Every binding path of an open-result local in a plain body, at T = Q (the
# result lends `a`), T = str and T = Own[Q] (it owns the result): the local
# is the twin's form, and its return lends at Q, moves at Own[Q].
from tpy import Fn, Own
from fixtures_q import Q, ident, own_id, sid


class Relay:
    def relay[T](self, f: Fn[[T], T], a: T) -> T:
        return f(a)


# first declaration borrowing `a`, then a rebind to a result
def mixed[T](f: Fn[[T], T], a: T) -> T:
    y = a
    y = f(y)  # tpyc: ok
    return y


# a branch-first binding: one arm a result, the other `a` itself
def branch[T](f: Fn[[T], T], a: T, c: bool) -> T:
    if c:
        y = f(a)  # tpyc: ok
    else:
        y = a
    return y


# a `try` body and a handler each binding it
def tried[T](f: Fn[[T], T], a: T, c: bool) -> T:
    try:
        if c:
            raise ValueError("x")
        y = f(a)  # tpyc: ok
    except ValueError:
        y = a
    return y


# a walrus binding, then a rebind
def walrus[T](f: Fn[[T], T], a: T) -> T:
    (y := f(a))  # tpyc: ok
    y = f(y)
    return y


# a walrus as the only binding, read once at the return
def walrus_once[T](f: Fn[[T], T], a: T) -> T:
    (y := f(a))  # tpyc: ok
    return y


# a tuple-unpack target, then a rebind
def unpacked[T](f: Fn[[T], T], a: T) -> T:
    y, k = f(a), 1  # tpyc: ok
    y = f(y)
    return y


# a read handed to a generic METHOD's open `T` (OPEN_SAME_T)
def via_method[T](r: Relay, f: Fn[[T], T], a: T) -> T:
    return r.relay(f, a)  # tpyc: ok


# a single-assignment local of a call's open result (`val_or_ref_t<T>`,
# not the rebound open-result local) as a list-literal element at its
# last use: at T = Own[Q] the fresh value moves in (`[f(a)]` itself and a
# rebound local are BUGS.md#open-t-call-list-literal-element-rejects)
def lit[T](f: Fn[[T], T], a: T) -> Own[list[T]]:
    y = f(a)
    return [y]  # tpyc: warning(/may copy T into owned storage/)


def show(tag: str, q: Q, out: Q) -> None:
    out.items.append(5)
    print(tag, len(q.items), len(out.items))


def own(tag: str, out: Q) -> None:
    out.items.append(5)
    print(tag, len(out.items))


def main() -> None:
    q = Q(1)
    show("mixed-ref", q, mixed(ident, q))
    print("mixed-str", mixed(sid, "a"))
    own("mixed-own", mixed(own_id, Q(2)))  # tpyc: warning(/copies Q into owned storage/)
    show("branch-ref-t", q, branch(ident, q, True))
    show("branch-ref-f", q, branch(ident, q, False))
    print("branch-str", branch(sid, "b", True), branch(sid, "c", False))
    own("branch-own-t", branch(own_id, Q(3), True))  # tpyc: warning(/copies Q into owned storage/)
    own("branch-own-f", branch(own_id, Q(4), False))  # tpyc: warning(/copies Q into owned storage/)
    show("try-ref-t", q, tried(ident, q, True))
    show("try-ref-f", q, tried(ident, q, False))
    print("try-str", tried(sid, "d", True), tried(sid, "e", False))
    own("try-own-t", tried(own_id, Q(5), True))  # tpyc: warning(/copies Q into owned storage/)
    own("try-own-f", tried(own_id, Q(6), False))  # tpyc: warning(/copies Q into owned storage/)
    show("walrus-ref", q, walrus(ident, q))
    print("walrus-str", walrus(sid, "f"))
    own("walrus-own", walrus(own_id, Q(7)))  # tpyc: warning(/copies Q into owned storage/)
    w = Q(1)
    show("walrus-once-ref", w, walrus_once(ident, w))
    print("walrus-once-str", walrus_once(sid, "k"))
    show("unpack-ref", q, unpacked(ident, q))
    print("unpack-str", unpacked(sid, "g"))
    own("unpack-own", unpacked(own_id, Q(8)))  # tpyc: warning(/copies Q into owned storage/)
    r = Relay()
    show("method-ref", q, via_method(r, ident, q))
    print("method-str", via_method(r, sid, "j"))
    own("method-own", via_method(r, own_id, Q(11)))
    ls = lit(own_id, Q(12))
    own("literal-own", ls[0])

