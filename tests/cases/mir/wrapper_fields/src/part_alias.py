# Holders of an Optional field's payload across a write through a second parameter or a callee.
from tpy import int32
from fixtures import H, P, clear


# free function conflict: `a` and `b` may name one holder; emptying b.p ends what o holds (a no-alias obligation)
def alias(a: H, b: H, flag: bool) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    o = a.p
    if flag:
        b.p = None
    if o is not None:
        return o.x
    return 0


# witness, never run: the caller passes one holder as both, so the obligation `alias` publishes is a warning
def alias_call(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return alias(h, h, True)  # tpyc: warning(/argument 'h' contains 'h.p', which 'alias' modifies through parameter 'b' while it still uses 'a'/)


# safe sibling: the holder is read before b.p is emptied
def alias_read_first(a: H, b: H) -> int32:  # tpyc: mir(covered)
    o = a.p  # tpyc: mir_borrows(o, a.p[payload]) mir_copy(o)
    r = 0
    if o is not None:
        r = o.x
    b.p = None
    return r


# safe sibling: a write to a different field ends nothing the holder holds
def alias_other_field(a: H, b: H) -> int32:  # tpyc: mir(covered)
    o = a.p  # tpyc: mir_borrows(o, a.p[payload])
    b.n = 1
    if o is not None:
        return o.x
    return 0


# free function conflict: the callee empties the field the holder borrows from
def callee(h: H, flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    o = h.p
    if flag:
        clear(h)
    if o is not None:
        return o.x
    return 0


def main() -> None:
    a = H(P(1), 10)
    b = H(P(2), 20)
    print("alias", alias(a, b, False))
    print("alias_read_first", alias_read_first(a, b))
    print("alias_other_field", alias_other_field(a, b))
    print("callee", callee(a, False))
