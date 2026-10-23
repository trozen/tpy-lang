# Argument-alias warning: a borrowed argument pointing into storage the
# callee writes through another parameter is reported at the call
# (BUGS.md#borrowed-arg-into-mutated-storage).
# The warned calls are never run: each reads storage the callee replaced.
from typing import Iterator

from helpers import grow_then_read as gtr
from tpy import StrView, auto_readonly, copy, int32


class H:
    name: str
    n: int32

    def __init__(self, name: str) -> None:
        self.name = name
        self.n = 0

    def setname(self, value: str, other: str) -> int32:  # tpyc: mir(conflict /alias_obligation/)
        self.name = other
        return len(value)


def bound(events: list[int], tag: int, value: int) -> int:  # tpyc: mir(conflict /alias_obligation/)
    events.append(tag)
    return value


def bump(h: H, k: int32) -> int32:
    h.n = 5
    return k


def walk(src: list[int32], dst: list[int32]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    total = 0
    for v in src:
        dst.append(v)
        total += v
    return total


# int element of the list the callee grows: warns
def alias_int(xs: list[int]) -> int:  # tpyc: mir(conflict /argument_alias/)
    return bound(xs, 1, xs[0])  # tpyc: warning(/argument 'xs\[0\]' points into 'xs', which 'bound' modifies through parameter 'events' while it still uses 'value'/)


# a different list: silent, the obligation moves to this function's own (an alias_obligation conflict)
def other_int(dst: list[int], src: list[int]) -> int:  # tpyc: mir(conflict /alias_obligation/)
    return bound(dst, 1, src[0])  # tpyc: ok


# the moved obligation, broken by passing one list twice: warns
def propagated(xs: list[int]) -> int:  # tpyc: mir(conflict /argument_alias/)
    return other_int(xs, xs)  # tpyc: warning(/'xs' is passed as both 'src' and 'dst' of 'other_int', which modifies it through 'dst' while it still uses 'src'/)


# a local copy owns its storage: silent
def local_copy(xs: list[int]) -> int:  # tpyc: mir(covered)
    v = xs[0]
    return bound(xs, 1, v)  # tpyc: ok


# a local list is the caller's own storage: warns
def local_list() -> int32:  # tpyc: mir(conflict /argument_alias/)
    xs: list[int32] = [5, 6]
    return walk(xs, xs)  # tpyc: warning(/'xs' is passed as both 'src' and 'dst' of 'walk'/)


# a scalar field write does not replace storage the int argument points into: silent
def scalar_field(h: H) -> int32:  # tpyc: mir(covered)
    return bump(h, h.n)  # tpyc: ok


# one list iterated while the same list grows: warns
def iterate_grow(xs: list[int32]) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return walk(xs, xs)  # tpyc: warning(/'xs' is passed as both 'src' and 'dst' of 'walk', which modifies it through 'dst' while it still uses 'src'; pass a copy/)


# a list built in the body hands the iteration its own storage: silent
def copied_list(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    ys: list[int32] = []
    for v in xs:
        ys.append(v)
    return walk(ys, xs)  # tpyc: ok


# method receiver as the written parameter: the view of h.name dangles once setname replaces it: warns
def receiver_field(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return h.setname(h.name, "long-name-long-name-long-name")  # tpyc: warning(/argument 'h.name' is what 'H.setname' modifies through parameter 'self' while it still uses 'value'/)


# a slice of the field the method replaces: warns, the slice lying inside the field
def receiver_slice(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return h.setname(h.name[1:], "long-name-long-name-long-name")  # tpyc: warning(/argument 'h.name\[1:\]' points into 'h.name', which 'H.setname' modifies through parameter 'self'/)


# a field of another record: silent
def other_field(h: H, g: H) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    return h.setname(g.name, "long-name-long-name-long-name")  # tpyc: ok


# str remedy: a local binding of the field owns a copy: silent
def str_local(h: H) -> int32:  # tpyc: mir(covered)
    v = h.name
    return h.setname(v, "long-name-long-name-long-name")  # tpyc: ok


# list remedy: copy() hands the iteration its own list: silent (MIR does not lower copy() of a list yet)
def list_copy(xs: list[int32]) -> int32:  # tpyc: mir(uncovered /named argument needs complete temporary plan/)
    return walk(copy(xs), xs)  # tpyc: ok


class Point:
    x: int32
    name: str

    def __init__(self, x: int32, name: str) -> None:
        self.x = x
        self.name = name


class Line:
    a: Point

    def __init__(self, a: Point) -> None:
        self.a = copy(a)


def alias_external(left: Line, right: Line) -> bool:  # tpyc: mir(conflict /alias_obligation/)
    held = left.a
    right.a = Point(9, "new")
    return held.x > 0


# a member held through one parameter, replaced through the other, both one record: warns
def one_line(ln: Line) -> bool:  # tpyc: mir(conflict /argument_alias/)
    return alias_external(ln, ln)  # tpyc: warning(/argument 'ln' contains 'ln.a', which 'alias_external' modifies through parameter 'right' while it still uses 'left'; pass a copy/)


class Counter:
    items: list[int32]

    def __init__(self) -> None:
        self.items = []

    def merge_from(self, other: "Counter") -> None:  # tpyc: mir(conflict /alias_obligation/)
        for x in other.items:
            self.items.append(x)


# a method iterating its argument's list while growing its receiver's, one counter: warns
def merge_self(a: Counter) -> None:  # tpyc: mir(conflict /argument_alias/)
    a.merge_from(a)  # tpyc: warning(/argument 'a' contains 'a.items', which 'Counter.merge_from' modifies through parameter 'self' while it still uses 'other'/)


def rename(r: H, s: str) -> None:
    r.name = s


def forwarded_then_reuse(r: H, s: str) -> int:  # tpyc: mir(conflict /alias_obligation/)
    rename(r, s)
    return len(s)


# a view of the field a forwarded write replaces: warns
def forwarded(r: H) -> int:  # tpyc: mir(conflict /argument_alias/)
    return forwarded_then_reuse(r, r.name)  # tpyc: warning(/argument 'r.name' is what 'forwarded_then_reuse' modifies through parameter 'r' while it still uses 's'; bind it to a local first/)


class R:
    buf: str
    s: StrView

    def __init__(self, buf: str, s: StrView) -> None:
        self.buf = buf
        self.s = s


class Holder:
    inner: R

    def __init__(self, inner: R) -> None:
        self.inner = copy(inner)


def member_param(o: Holder, s: str) -> None:  # tpyc: mir(conflict /alias_obligation/)
    o.inner = R(s, s)


# the new member views the buffer of the member it replaces: warns
def member_self(o: Holder) -> None:  # tpyc: mir(conflict /argument_alias/)
    member_param(o, o.inner.buf)  # tpyc: warning(/argument 'o.inner.buf' points into 'o.inner', which 'member_param' modifies through parameter 'o'/)


# generator caller: not checked yet (BUGS.md#borrowed-arg-into-mutated-storage), MIR does not lower a resumable body
def gen_caller(xs: list[int]) -> Iterator[int]:  # tpyc: mir(uncovered /resumable body/)
    yield bound(xs, 1, xs[0])  # tpyc: ok


class Log:
    xs: list[int]

    def __init__(self) -> None:
        self.xs = [1]

    # method body as the caller: the receiver's list and its element: warns
    def record_first(self) -> int:  # tpyc: mir(conflict /argument_alias/)
        return bound(self.xs, 1, self.xs[0])  # tpyc: warning(/argument 'self.xs\[0\]' points into 'self.xs', which 'bound'/)


# a keyword argument that aliases: warns
def keyword_alias(xs: list[int]) -> int:  # tpyc: mir(conflict /argument_alias/)
    return bound(xs, 1, value=xs[0])  # tpyc: warning(/argument 'xs\[0\]' points into 'xs', which 'bound' modifies through parameter 'events' while it still uses 'value'/)


# an element at a name index: warns, spelled as written
def name_index(xs: list[int], i: int32) -> int:  # tpyc: mir(conflict /argument_alias/)
    return bound(xs, 1, xs[i])  # tpyc: warning(/argument 'xs\[i\]' points into 'xs'/)


# an element at a negative index: warns, spelled as written
def negative_index(xs: list[int]) -> int:  # tpyc: mir(conflict /argument_alias/)
    return bound(xs, 1, xs[-1])  # tpyc: warning(/argument 'xs\[-1\]' points into 'xs'/)


class Stack:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1]

    # @auto_readonly caller: both clone bodies find the call, which warns once
    @auto_readonly
    def top(self) -> list[int32]:  # tpyc: mir(conflict /argument_alias/)
        ys: list[int32] = [2, 3]
        walk(ys, ys)  # tpyc: warning(/'ys' is passed as both 'src' and 'dst' of 'walk', which modifies it through 'dst' while it still uses 'src'/)
        return self.items


# an argument with no simple spelling: warns with the parameter's name and no example
def unspelled(xs: list[int]) -> int:  # tpyc: mir(conflict /argument_alias/)
    return bound(xs, 1, xs[len(xs) - 1])  # tpyc: warning(/the argument for parameter 'value' points into 'xs', .* bind it to a local first or restructure/)


# a callee imported under another name: warns, named by its definition
def renamed(xs: list[int]) -> int:  # tpyc: mir(conflict /argument_alias/)
    return gtr(xs, 1, xs[0])  # tpyc: warning(/argument 'xs\[0\]' points into 'xs', which 'grow_then_read'/)


# record remedy: copy() hands the callee its own record: silent (MIR does not lower this caller yet)
def line_copy(ln: Line) -> bool:  # tpyc: mir(uncovered /named argument needs complete temporary plan/)
    return alias_external(ln, copy(ln))  # tpyc: ok


# container_select: a container alias of a conditional is analyzed like its record twin
class Cell:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def pick(c: bool, a: list[Cell], b: list[Cell]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    v = a if c else b
    r = v[0]
    a.append(Cell(5))
    return r.x


def caller(xs: list[Cell]) -> int32:  # tpyc: mir(conflict /argument_alias/)
    # The alias `v` may be `a`, whose growth invalidates `r`: one list as both arms is warned, not refused.
    return pick(True, xs, xs)  # tpyc: warning(/passed as both/)


# element identity is not tracked: the callee replaces an element while it
# still reads a borrow into one, so a borrow into any element warns
class Named:
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


def put(ps: list[Named], value: str) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    ps[0] = Named("new")
    return len(value)


def put_int(xs: list[int], value: int) -> int:  # tpyc: mir(conflict /alias_obligation/)
    xs[0] = 5
    return value


def same_element(ps: list[Named]) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return put(ps, ps[0].s)  # tpyc: warning(/argument 'ps\[0\]\.s' points into 'ps', which 'put' modifies through parameter 'ps' while it still uses 'value'/)


def same_int_element(xs: list[int]) -> int:  # tpyc: mir(conflict /argument_alias/)
    return put_int(xs, xs[0])  # tpyc: warning(/argument 'xs\[0\]' points into 'xs', which 'put_int' modifies through parameter 'xs' while it still uses 'value'/)


# a callee whose summary publishes a field write inside an element (ps[elements].s)
def touch(ps: list[Named], value: str) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    for p in ps:
        p.s = "x"
    return len(value)


# the borrowed field of an element is what the callee's element field write replaces: warns
def element_field(ps: list[Named]) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return touch(ps, ps[0].s)  # tpyc: warning(/argument 'ps\[0\]\.s' is what 'touch' modifies through parameter 'ps' while it still uses 'value'/)


def main() -> None:
    print("other_int", other_int([1], [2]))
    print("local_copy", local_copy([3]))
    print("scalar_field", scalar_field(H("a")))
    print("walk", walk([1, 2], []))
    print("other_field", other_field(H("a"), H("b")))
    print("copied_list", copied_list([7, 8]))
    print("str_local", str_local(H("abc")))
    print("list_copy", list_copy([4, 5]))
    print("forwarded_then_reuse", forwarded_then_reuse(H("a"), "bcd"))
    print("alias_external", alias_external(Line(Point(1, "p")), Line(Point(2, "q"))))
    c, d = Counter(), Counter()
    d.items.append(3)
    c.merge_from(d)
    print("merge_from", len(c.items))
    o = Holder(R("x", "y"))
    member_param(o, "zz")
    print("member_param", o.inner.buf)
    print("line_copy", line_copy(Line(Point(5, "r"))))
    cells = [Cell(1)]
    others = [Cell(2)]
    print("container_select pick b:", pick(False, cells, others))
    # The callee grew the caller's list in place.
    print("container_select grown:", len(cells), cells[1].x)
    others[0].x = 9
    print("container_select pick b again:", pick(False, cells, others), len(cells))
    named = [Named("a"), Named("b")]
    print("touch", touch(named, "abc"), named[0].s, named[1].s)


main()
