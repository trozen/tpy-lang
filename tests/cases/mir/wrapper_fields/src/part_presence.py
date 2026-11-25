# Presence proofs of an Optional field killed or kept by writes of other places.
from tpy import int32
from fixtures import H, HL, P, T, clear, mutate


# kept refusal: sema drops the narrowing at the method call, so the read is unproven
def fresh(h: H, flag: bool) -> int32:  # tpyc: mir(uncovered /unproven optional field read/)
    if h.p is not None:
        if flag:
            h.reset()
        return h.p.x  # tpyc: warning(/Potential None access/)
    return 0


# presence kill: b may be a, so emptying b.p ends the proof that a.p is engaged
def fresh_alias(a: H, b: H) -> int32:  # tpyc: mir(uncovered /presence proof/)
    if a.p is not None:
        b.p = None
        return a.p.x
    return 0


# presence kill: the callee may empty b.p, which may be a.p (its published write of param0.p)
def fresh_call(a: H, b: H) -> int32:  # tpyc: mir(uncovered /presence proof/)
    if a.p is not None:
        clear(b)
        return a.p.x
    return 0


def empties(h: H) -> bool:  # tpyc: mir(covered) mir_summary(known)
    h.p = None
    return True


# presence kill: the proof stored on the `and` operand dies at the callee's write of b.p
def fresh_condition(a: H, b: H) -> int32:  # tpyc: mir(uncovered /presence proof/)
    if a.p is not None and empties(b):
        return a.p.x
    return 0


# presence kept: writing a different field leaves a.p engaged
def keep_other_field(a: H, b: H) -> int32:  # tpyc: mir(covered)
    if a.p is not None:
        b.n = 2
        return a.p.x
    return 0


# presence kept: the body's own record is never the parameter's object
def own_field_write(a: H) -> int32:  # tpyc: mir(covered)
    q = P(1)
    h = H(q, 1)
    if a.p is not None:
        h.p = None
        return a.p.x + h.n
    return 0


# presence kept: two of the body's own records, each through a local holder, are distinct storage
def holders() -> int32:  # tpyc: mir(covered)
    q = P(1)
    x = H(q, 2)
    r = P(3)
    y = H(r, 4)
    hx = x
    hy = y
    if hx.p is not None:
        hy.p = None
        return hx.p.x
    return 0


# presence kept: a P field never holds an H's member; replacing b.q may still free what `a`
# holds if `b` is `a` (retention's coarse oracle), published as a no-alias obligation
def keep_record_field(a: HL, b: HL) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    if a.p is not None:
        b.q = P(3)
        return a.p.x
    return 0


# presence kept: a str field holds no record
def keep_leaf_field(a: HL, b: HL) -> int32:  # tpyc: mir(covered)
    if a.p is not None:
        b.s = "z"
        return a.p.x
    return 0


# presence kept: a root Optional's proof is the slot's own, so a field write that ends h.p's proof leaves o's
def keep_root(h: H, x: int32) -> int32:  # tpyc: mir(covered)
    o: P | None = P(x)
    if o is not None:
        if h.p is not None:
            h.p = None
            return o.x
    return 0


# presence kept: the root fact on the `and`'s outcome, carried by `ok`, survives the write that ends h.p's
# (`o` is the body's own, so retention sees no loan the write could end)
def keep_root_condition(h: H, x: int32) -> int32:  # tpyc: mir(covered)
    o: P | None = P(x)
    ok = o is not None and h.p is not None
    h.p = None
    if ok:
        return o.x
    return 0


# presence kept: initializing the body's own records replaces nothing a.p's proof rests on
def keep_initialization(a: H) -> int32:  # tpyc: mir(covered)
    if a.p is not None:
        q = P(1)
        h = H(q, 2)
        return a.p.x + h.n
    return 0


# presence kept: a record local filled on one branch -- its empty backing filled by an unengaged Optional assignment -- beside a.p's proof
def keep_backing(a: H, flag: bool) -> int32:  # tpyc: mir(covered)
    if a.p is not None:
        if flag:
            r = P(5)
        else:
            return 0
        return a.p.x + r.x
    return 0


# presence kept: a loop's iterator stays valid across a field write in its body
def loop_write(h: H) -> int32:  # tpyc: mir(covered)
    n = 0
    xs = [1, 2]
    for x in xs:
        n += x
        if h.p is not None:
            h.p = None
    return n


def eat_opt_p(o: P | None, s: str) -> int32:  # tpyc: mir(covered)
    if o is not None:
        return o.x + len(s)
    return len(s)


# argument order: an Optional record built from a writing call is unordered against the view of
# h.t[1], refused as its plain-record twin is
def order_opt_record(h: T) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    return eat_opt_p(P(mutate(h, False)), h.t[1])


# argument order: the same Optional record argument writing nothing stays covered
def order_opt_pure(h: T) -> int32:  # tpyc: mir(covered)
    return eat_opt_p(P(2), h.t[1])


def main() -> None:
    a = H(P(1), 10)
    b = H(P(2), 20)
    print("fresh", fresh(H(P(3), 30), False))
    print("fresh_alias", fresh_alias(H(P(4), 40), H(P(5), 50)))
    print("fresh_call", fresh_call(H(P(6), 60), H(P(7), 70)))
    print("keep_other_field", keep_other_field(a, b))
    print("fresh_condition", fresh_condition(H(P(12), 1), H(P(13), 2)))
    print("own_field_write", own_field_write(a))
    print("keep_root_condition", keep_root_condition(H(P(20), 1), 19))
    print("keep_initialization", keep_initialization(a))
    print("keep_backing", keep_backing(a, True))
    print("holders", holders())
    hl = HL(P(16), P(17), "hl")
    print("keep_record_field", keep_record_field(hl, HL(None, P(18), "other")))
    print("keep_leaf_field", keep_leaf_field(hl, hl))
    print("keep_root", keep_root(H(P(19), 1), 20))
    print("loop_write", loop_write(H(P(21), 1)))
    print("order_opt_record", order_opt_record(T()))
    print("order_opt_pure", order_opt_pure(T()))
