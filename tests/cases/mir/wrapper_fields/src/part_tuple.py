# Tuple fields of scalars and an owned leaf (`t: tuple[int32, str]`): a place of their record,
# whose str member a view holds. A warned section reads a freed string (CPython keeps the old
# object): pinned, never run.
from typing import Iterator
from tpy import StrView, char, int32, readonly
from fixtures import P, T, mutate


class TS:
    t: tuple[int32, int32]

    # constructor: a scalar tuple field
    def __init__(self, x: int32) -> None:  # tpyc: mir(covered)
        self.t = (x, x + 1)

    # method: a scalar tuple field rewritten through self
    def reset(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.t = (5, 6)  # tpyc: mir_write(self.t)


class TP:
    q: tuple[P, int32]

    # constructor: a tuple field storing a record inline
    def __init__(self) -> None:  # tpyc: mir(covered)
        self.q = (P(2), 3)


def refill(h: T) -> None:  # tpyc: mir(covered) mir_summary(known)
    h.t = (2, "b" * 3)  # tpyc: mir_write(h.t)


# never run: the field replaced while a view of its str member is live
def view_write(h: T) -> None:  # tpyc: mir(conflict /replacement/)
    s: StrView = h.t[1]
    h.t = (2, "b" * 3)  # tpyc: warning(/this assignment frees the old value of 'h.t', but 's' still views it/)
    print("view_write", s)


# never run: a callee replaces the field under the view
def view_callee(h: T) -> None:  # tpyc: mir(conflict /replacement/)
    s: StrView = h.t[1]
    refill(h)  # tpyc: warning(/this call frees the old value of 'h.t', but 's' still views it/)
    print("view_callee", s)


# a may be b: copying c.t into b.t may free what s views (a no-alias obligation)
def cross_copy(a: T, b: T, c: T) -> int32:  # tpyc: mir(conflict /alias_obligation/) mir_summary(known)
    s: StrView = a.t[1]
    b.t = c.t  # tpyc: mir_copy(b.t)
    return len(s)


# never run: the caller passes one holder as both
def cross_copy_call(h: T, g: T) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return cross_copy(h, h, g)  # tpyc: warning(/argument 'h' contains 'h.t', which 'cross_copy' modifies through parameter 'b' while it still uses 'a'/)


# never run: the holder ends with its block while the view of its member is live
def inner_scope(flag: bool) -> None:  # tpyc: mir(conflict /scope/)
    s: StrView = "x"
    if flag:
        h = T()
        s = h.t[1]  # tpyc: warning(/the object made for 'h' is freed at the end of this block, but 's' still views it/)
    print("inner_scope", s)


# replacing h.q while p may point into it (a no-alias obligation); a caller passing `h.q[0]`
# is a THIR reject today (BUGS.md#tuple-field-record-member-arg-rejects)
def kill_member(h: TP, p: P) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.q = (P(5), 6)
    return p.x


# replacing h.t while v may view its str member (a no-alias obligation), as a `str` field's twin
def kill_str(h: T, v: str) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.t = (2, "c" * 2)
    return len(v)


# never run: the caller passes the member itself, which the callee frees
def kill_str_call(h: T) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return kill_str(h, h.t[1])  # tpyc: warning(/argument 'h.t\[1\]' points into 'h.t', which 'kill_str' modifies through parameter 'h' while it still uses 'v'/)


# a view of the str member returned: published at the member's path
def member_view(h: T) -> StrView:  # tpyc: mir(covered) mir_summary(known)
    return h.t[1]


# never run: the caller holds the view a callee returned while it replaces the field
def member_view_call(h: T) -> None:  # tpyc: mir(conflict /replacement/)
    s: StrView = member_view(h)
    h.t = (3, "d" * 2)  # tpyc: warning(/Mutation of 'h' while borrowed/) warning(/this assignment frees the old value of 'h.t', but 's' still views it/)
    print("member_view_call", s)


def eat(t: tuple[int32, int32], s: str) -> int32:  # tpyc: mir(covered)
    return t[0] + len(s)


def eat_scalar(n: int32, s: str) -> int32:  # tpyc: mir(covered)
    return n + len(s)


# argument order: a tuple argument whose member writes h.t is unordered against the view of
# h.t[1], refused as its scalar twin below is
def order_tuple(h: T) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    return eat((mutate(h, False), 0), h.t[1])


def order_scalar(h: T) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    return eat_scalar(mutate(h, False), h.t[1])


def get_str(n: int32) -> str:  # tpyc: mir(covered)
    return "hello"


def eat_view(s: str) -> int32:  # tpyc: mir(covered)
    return len(s) - 2


# indexing order: the receiver's call writes h.t while the index reads h.t[1]; MIR splices the
# receiver first, so this order is unmodeled and refused
def idx_recv_writes(h: T) -> char:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    return get_str(mutate(h, False))[eat_view(h.t[1])]


# indexing order: the index's call writes h.t while the receiver h.t[1]'s loan is live, which
# MIR sees and warns about
def idx_index_writes(h: T) -> char:  # tpyc: mir(conflict /replacement/)
    return h.t[1][mutate(h, False)]  # tpyc: warning(/overwrites 'h.t' in place/)


# indexing order: a receiver writing nothing stays covered
def idx_pure(h: T) -> char:  # tpyc: mir(covered)
    return get_str(1)[eat_view(h.t[1])]


# indexing order: a writing receiver beside a literal index has no order to get wrong
def idx_literal(h: T) -> char:  # tpyc: mir(covered)
    return get_str(mutate(h, False))[0]


# reads through a readonly receiver
def ro_read(h: readonly[T]) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return h.t[0] + len(h.t[1])


# a whole copy of another holder's field
def copy_field(a: T, b: T) -> None:  # tpyc: mir(covered) mir_summary(known)
    a.t = b.t  # tpyc: mir_copy(a.t) mir_write(a.t)


# generator: a tuple field's members read and the field rewritten across suspensions
def members(h: TS) -> Iterator[int32]:  # tpyc: mir(covered)
    yield h.t[0]
    h.t = (7, 8)
    yield h.t[1]


def main() -> None:
    h = T()
    g = T()
    refill(g)
    copy_field(h, g)
    print("tuple ro_read", ro_read(h))
    print("tuple member_view", member_view(h))
    print("tuple cross_copy", cross_copy(h, T(), g))
    print("tuple order", order_tuple(h), order_scalar(h))
    print("tuple idx", idx_recv_writes(h), idx_index_writes(h), idx_pure(h), idx_literal(h))
    s = TS(3)
    print("tuple scalar", s.t[0], s.t[1])
    s.reset()
    for x in members(s):
        print("tuple members", x)
    q = TP()
    print("tuple record", q.q[0].x, q.q[1])
