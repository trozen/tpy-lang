# Containers of tuple, Optional and union elements (`list[tuple[int32, str]]`, `list[int32 | None]`):
# an element's members are places of the elements region. Warned sections read a freed or moved
# string (CPython keeps the old object): pinned, never run.
from tpy import StrView, int32
from fixtures import P


class R:
    t: tuple[int32, int32]

    # constructor: a record element's tuple field
    def __init__(self, x: int32) -> None:  # tpyc: mir(covered)
        self.t = (x, x)


def clears(xs: list[tuple[int32, str]]) -> None:  # tpyc: mir(covered) mir_summary(known)
    xs.clear()


# never run: clear() under a live view of an element's str member
def clear_view(xs: list[tuple[int32, str]]) -> None:  # tpyc: mir(conflict /replacement/)
    s: StrView = xs[0][1]
    xs.clear()  # tpyc: warning(/this call may move the elements of 'xs', but 's' still views them/)
    print("clear_view", s)


# never run: a callee clears under the view
def callee_clear(xs: list[tuple[int32, str]]) -> None:  # tpyc: mir(conflict /replacement/)
    s: StrView = xs[0][1]
    clears(xs)  # tpyc: warning(/this call may move the elements of 'xs', but 's' still views them/)
    print("callee_clear", s)


# never run: a dict value replaced under a view of its str member, as a `str` value's twin
def dict_set(d: dict[int32, tuple[int32, str]]) -> None:  # tpyc: mir(conflict /replacement/)
    s: StrView = d[1][1]
    d[1] = (2, "b" * 2)  # tpyc: warning(/this assignment may free the element 's' still views/)
    print("dict_set", s)


# a list element replaced, as a `str` element's twin
def list_set(xs: list[tuple[int32, str]]) -> None:  # tpyc: mir(covered) mir_summary(known)
    xs[0] = (2, "b" * 2)


# comprehension: built element by element, then read
def comprehension(n: int32) -> int32:  # tpyc: mir(covered)
    xs = [(i, "x" * i) for i in range(n)]
    return len(xs[n - 1][1])


# dict comprehension: the list comprehension's twin
def dict_comprehension(n: int32) -> int32:  # tpyc: mir(covered)
    d = {i: (i, "x" * i) for i in range(n)}
    return len(d[n - 1][1])


# never run: a comprehension's result cleared under a view of an element
def comprehension_clear(n: int32) -> None:  # tpyc: mir(conflict /replacement/)
    xs = [(i, "x" * i) for i in range(n)]
    s: StrView = xs[0][1]
    xs.clear()  # tpyc: warning(/this call may move the elements of 'xs', but 's' still views them/)
    print("comprehension_clear", s)


# a literal of scalar tuples, read in place
def scalar_literal() -> int32:  # tpyc: mir(covered)
    xs = [(1, 2), (3, 4)]
    return xs[1][0]


# a list of records with a tuple field
def records() -> int32:  # tpyc: mir(covered)
    rs = [R(1), R(2)]
    r = rs[1]
    return r.t[0]


# never run: a list element replaced under a view of its str member, as the dict twin
def list_view_set(xs: list[tuple[int32, str]]) -> None:  # tpyc: mir(conflict /replacement/)
    s: StrView = xs[0][1]
    xs[0] = (2, "b" * 2)  # tpyc: warning(/this assignment may free the element 's' still views/)
    print("list_view_set", s)


class SP:
    p: P | None

    # constructor: a record element's Optional record field
    def __init__(self) -> None:  # tpyc: mir(covered)
        self.p = None


# Optional elements written: `[elements]` names any element, so the write states no outcome
def set_opts(xs: list[int32 | None], d: dict[int32, int32 | None]) -> None:  # tpyc: mir(covered) mir_summary(known)
    xs[0] = None  # tpyc: mir_write(xs[elements])
    d[1] = None  # tpyc: mir_write(d[elements])


# Optional elements engaged in place
def set_engaged(xs: list[int32 | None], d: dict[int32, int32 | None]) -> None:  # tpyc: mir(covered) mir_summary(known)
    xs[0] = 5  # tpyc: mir_write(xs[elements])
    d[1] = 5  # tpyc: mir_write(d[elements])


def set_opts_call(xs: list[int32 | None], d: dict[int32, int32 | None]) -> None:  # tpyc: mir(covered)
    set_opts(xs, d)


# an owned-leaf Optional element emptied in place (engaging one is a THIR reject,
# BUGS.md#optional-str-element-setitem-rejects)
def set_str_none(xs: list[str | None]) -> None:  # tpyc: mir(covered) mir_summary(known)
    xs[0] = None  # tpyc: mir_write(xs[elements])


# an element's Optional field written through the elements region: no definite outcome either
def set_elem_field(xs: list[SP]) -> None:  # tpyc: mir(covered) mir_summary(known)
    xs[0].p = P(2)


def set_elem_field_call(xs: list[SP]) -> None:  # tpyc: mir(covered)
    set_elem_field(xs)


# Optional and union elements cleared
def clear_cells(xs: list[int32 | None], us: list[int32 | str]) -> None:  # tpyc: mir(covered) mir_summary(known)
    xs.clear()
    us.clear()


# an Optional element tested in place is refused (a union element write is a THIR reject,
# BUGS.md#setitem-value-union-elem)
def read_opt(xs: list[int32 | None]) -> bool:  # tpyc: mir(uncovered /unsupported optional test/)
    return xs[0] is None


def main() -> None:
    xs = [(1, "a" * 2), (2, "b" * 2)]
    list_set(xs)
    print("elements list_set", xs[0][0], xs[0][1])
    d = {1: (1, "a" * 2)}
    print("elements dict", d[1][1])
    print("elements comprehension", comprehension(3), dict_comprehension(3))
    print("elements scalar_literal", scalar_literal())
    print("elements records", records())
    clears(xs)
    print("elements clears", len(xs))
    os: list[int32 | None] = [1, 2]
    od: dict[int32, int32 | None] = {1: 1}
    set_opts_call(os, od)
    print("elements set_opts", os[0] is None, od[1] is None, read_opt(os))
    set_engaged(os, od)
    print("elements set_engaged", os[0] is None, od[1] is None)
    ss: list[str | None] = ["x", "y"]
    set_str_none(ss)
    print("elements set_str_none", len(ss))
    sps = [SP()]
    set_elem_field_call(sps)
    us: list[int32 | str] = [1, "a"]
    clear_cells(os, us)
    print("elements cells", len(os), len(us), len(sps))
