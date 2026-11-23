# Argument-alias warning for an argument that is a tuple's member (`t[0]`):
# named as the storage it borrows, as its field or element twin is.
# The warned calls are never run: each reads storage the callee replaced.
from tpy import int32


class Q:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Hq:
    q: Q

    def __init__(self) -> None:
        self.q = Q(1)


def replace_then(h: Hq, q: Q) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.q = Q(5)
    return q.v


def clear_then(xs: list[Q], q: Q) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    xs.clear()
    return q.v


# a member borrowing the field the callee replaces IS that field, as `h.q` is: warns
def member_of_field(h: Hq) -> int32:  # tpyc: mir(conflict /argument_alias/)
    t = (h.q, 1)
    return replace_then(h, t[0])  # tpyc: warning(/argument 't\[0\]' is what 'replace_then' modifies through parameter 'h' while it still uses 'q'/)


# a member borrowing an element of the list the callee clears points into it, as `xs[0]` does: warns
def member_of_element(xs: list[Q]) -> int32:  # tpyc: mir(conflict /argument_alias/)
    t = (xs[0], 1)
    return clear_then(xs, t[0])  # tpyc: warning(/Passing borrowed container 'xs'/) warning(/argument 't\[0\]' points into 'xs', which 'clear_then' modifies through parameter 'xs' while it still uses 'q'/)


def replace_pair(h: Hq, pair: tuple[Q, int32]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.q = Q(5)
    return pair[0].v


def replace_opt(h: Hq, q: Q | None) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.q = Q(5)
    return q.v if q is not None else 0


# a member wrapped in a tuple argument IS the field, as the wrapped `h.q` is: warns
def member_in_tuple(h: Hq) -> int32:  # tpyc: mir(conflict /argument_alias/)
    t = (h.q, 1)
    return replace_pair(h, (t[0], 2))  # tpyc: warning(/argument 't\[0\]' is what 'replace_pair' modifies through parameter 'h' while it still uses 'pair'/)


# a member passed at an Optional parameter IS the field, as `h.q` is: warns
def member_as_optional(h: Hq) -> int32:  # tpyc: mir(conflict /argument_alias/)
    t = (h.q, 1)
    return replace_opt(h, t[0])  # tpyc: warning(/argument 't\[0\]' is what 'replace_opt' modifies through parameter 'h' while it still uses 'q'/)


def main() -> None:
    h = Hq()
    print("tuple_member replace_then", replace_then(h, Q(2)), h.q.v)
    xs = [Q(3)]
    print("tuple_member clear_then", clear_then(xs, Q(4)), len(xs))
