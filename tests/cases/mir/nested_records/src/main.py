# MIR pins for nested records: a record with inline record fields -- member
# initializers, whole-member writes, member borrows, copies and results, callers.
from tpy import int32, Own, readonly, copy


class Point:
    x: int32
    name: str

    def __init__(self, x: int32, name: str) -> None:  # tpyc: mir(covered)
        self.x = x
        self.name = name


class Line:
    a: Point
    b: Point
    tag: int32

    # constructor: a member copied from a borrowed record parameter, a member moved
    # from an Own parameter
    def __init__(self, a: Point, b: Own[Point], tag: int32) -> None:  # tpyc: mir(covered)
        self.a = copy(a)
        self.b = b
        self.tag = tag

    # method: a member returned by reference (return origin param0.a)
    @readonly
    def first(self) -> Point:  # tpyc: mir(covered) mir_summary(known)
        return self.a

    # method: a whole member replaced (a published write of param0.a)
    def reset(self, x: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.a = Point(x, "r")  # tpyc: mir_write(self.a)

    # method: a scalar inside a member written (param0.a.x: no replacement event)
    def bump(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.a.x += 1

    # method: a copy out of a member, a member-to-member copy, a local copied in
    def swap(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        t = copy(self.a)
        self.a = copy(self.b)
        self.b = copy(t)


class Built:
    p: Point
    n: int32

    # constructor: a nested construct over the parameters (a composed initializer)
    def __init__(self, x: int32, name: str) -> None:  # tpyc: mir(covered)
        self.p = Point(x, name)
        self.n = x


class Names:
    first: str
    second: str

    def __init__(self, first: str, second: str) -> None:  # tpyc: mir(covered)
        self.first = first
        self.second = second


class WrappedNames:
    names: Names

    # constructor: one parameter in two borrowed legs of a nested construct (two copies)
    def __init__(self, s: str) -> None:  # tpyc: mir(covered)
        self.names = Names(s, s)


class Frame:
    line: Line
    depth: int32

    # constructor: a record with record members, moved from an Own parameter
    def __init__(self, line: Own[Line], depth: int32) -> None:  # tpyc: mir(covered)
        self.line = line
        self.depth = depth


# free function: reads through members of a borrowed parameter
def read_path(ln: Line) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return ln.a.x + ln.b.x


# free function: reads two members deep
def read_deep(f: Frame) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return f.line.a.x + f.depth


# free function: a scalar inside a member written (published as param0.a.x)
def write_scalar(ln: Line) -> None:  # tpyc: mir(covered) mir_summary(known)
    ln.a.x = 7


# free function: a whole member replaced (published as param0.a)
def replace_member(ln: Line) -> None:  # tpyc: mir(covered) mir_summary(known)
    ln.a = Point(9, "n")  # tpyc: mir_write(ln.a) mir_owned(ln.a)


# free function: a member holder written through, the write observed through the
# owner -- after a silent copy of the member the owner would keep its old value
def mutate_through_member(ln: Line) -> int32:  # tpyc: mir(certified) mir_summary(known)
    held = ln.a  # tpyc: mir_borrows(held, ln.a)
    held.x = 50
    return ln.a.x


# The conflict sections below read the holder after the member is replaced: TPy
# reads the new member, CPython the old object, so each returns a predicate both
# runtimes agree on.

# free function, conflict: the member replaced while a holder borrows it
def replace_live(ln: Line) -> bool:  # tpyc: mir(conflict /replacement/) mir_summary(known)
    held = ln.a  # tpyc: mir_borrows(held, ln.a)
    ln.a = Point(9, "new")  # tpyc: warning(/Mutation of 'ln.a' while borrowed/)
    return held.x > 0


# free function, conflict: the member of a parameter that may alias the holder's owner
# (unwarned divergence: BUGS.md#aliased-record-params-member-replaced)
def alias_external(left: Line, right: Line) -> bool:  # tpyc: mir(conflict /replacement/) mir_summary(known)
    held = left.a
    right.a = Point(9, "new")  # tpyc: mir_write(right.a)
    return held.x > 0


# free function, conflict: replacing an ancestor reaches a holder two members down
def replace_ancestor(f: Frame) -> bool:  # tpyc: mir(conflict /replacement/) mir_summary(known)
    held = f.line.first()
    f.line = Line(Point(1, "a"), Point(2, "b"), 0)  # tpyc: warning(/Mutation of 'f.line' while borrowed/) mir_write(f.line)
    return held.x > 0


# free function, conflict: a member method's published write under the holder's prefix
# (unwarned divergence: BUGS.md#field-loan-whole-record-callee-unchecked)
def member_receiver(f: Frame) -> bool:  # tpyc: mir(conflict /replacement/) mir_summary(known)
    held = f.line.first()
    f.line.reset(9)
    return held.x > 0


# free function, conflict: the member replaced through the callee's summary
# (unwarned divergence: BUGS.md#field-loan-whole-record-callee-unchecked)
def reset_live(ln: Line) -> bool:  # tpyc: mir(conflict /replacement/) mir_summary(known)
    held = ln.a
    ln.reset(4)  # tpyc: mir_write(ln.a)
    return held.x > 0


# free function: a sibling member survives the replacement
def sibling_survives(ln: Line) -> int32:  # tpyc: mir(certified) mir_summary(known)
    held = ln.b  # tpyc: mir_borrows(held, ln.b)
    ln.a = Point(9, "new")
    return held.x


# free function: a scalar write through a call replaces no storage, so a holder
# of the very member the call writes under stays valid (and sees the write)
def scalar_call_is_not_replacement(ln: Line) -> int32:  # tpyc: mir(certified) mir_summary(known)
    held = ln.a  # tpyc: mir_borrows(held, ln.a)
    ln.bump()
    return held.x


# free function: a copy out of a member does not replace its source
def copy_does_not_replace_source(ln: Line) -> int32:  # tpyc: mir(certified) mir_summary(known)
    held = ln.a
    independent = copy(ln.a)
    return held.x + independent.x


# free function: the copy owns its storage, so replacing the member cannot reach it
def copy_then_replace(ln: Line) -> int32:  # tpyc: mir(covered) mir_summary(known)
    p = copy(ln.a)  # tpyc: mir_borrowed(p) mir_borrows(p, p)
    ln.a = Point(5, "c")
    return p.x


# free function: an owned str copied out of a member, then the member replaced
def name_then_replace(ln: Line) -> int32:  # tpyc: mir(covered) mir_summary(known)
    n = ln.a.name
    ln.a = Point(1, "z")
    return len(n)


# free function: a method's member result borrows the argument's member; a write
# through the owner afterwards is seen through it -- a silent copy would not see it
def member_result(ln: Line) -> int32:  # tpyc: mir(certified) mir_summary(known)
    p = ln.first()  # tpyc: mir_borrows(p, ln.a)
    ln.a.x += 100
    return p.x


# free function: a record with record members built and returned by value
def build(x: int32) -> Own[Line]:  # tpyc: mir(covered) mir_summary(known)
    p = Point(x, "p")
    return Line(p, Point(x + 1, "q"), 0)


# free function: a handed-over call result as a construct's record member
def build_frame(x: int32) -> int32:  # tpyc: mir(covered) mir_summary(known)
    f = Frame(build(x), 2)
    return f.line.b.x + f.depth


# free function: an owned local, written and called through its members
def owned_local_path() -> int32:  # tpyc: mir(covered) mir_summary(known)
    ln = Line(Point(1, "a"), Point(2, "b"), 3)
    ln.a.x = 10
    ln.reset(4)
    ln.bump()
    ln.swap()
    return ln.a.x + ln.b.x + ln.tag


# free function: an Own record parameter moved into a member
def adopt(f: Frame, ln: Own[Line]) -> None:  # tpyc: mir(covered) mir_summary(known)
    f.line = ln


# free function: a member of an Own parameter read after the parameter is copied
# (THIR spells the copy); the parameter is never moved, so sema warns
def keep_and_store(dst: Frame, src: Own[Line]) -> int32:  # tpyc: warning(/never consumed/) mir(certified) mir_summary(known)
    held = src.a
    dst.line = copy(src)
    return len(held.name)


# free function: composed constructs through callers
def composed(x: int32, s: str) -> int32:  # tpyc: mir(covered) mir_summary(known)
    b = Built(x, s)
    w = WrappedNames(s)
    return b.p.x + b.n + len(w.names.first) + len(w.names.second)


# free caller: temporaries handed over at Own parameters, to a callee that moves
# its parameter into a member and to one that copies it
def hand_over_callers() -> int32:  # tpyc: mir(covered) mir_summary(known)
    f = Frame(build(5), 1)
    adopt(f, build(8))
    n = keep_and_store(f, build(9))
    return f.line.a.x + n


# free caller, conflict: a holder under the member a callee replaces by a move
# (unwarned divergence: BUGS.md#field-loan-whole-record-callee-unchecked)
def adopt_live() -> bool:  # tpyc: mir(conflict /replacement/) mir_summary(known)
    f = Frame(build(5), 1)
    held = f.line.first()
    adopt(f, build(8))
    return held.x > 0


# module driver: a print beside a writing call evaluates eagerly in an order MIR
# does not model
def main() -> None:  # tpyc: mir(uncovered /^order-sensitive eager operands$/)
    ln = Line(Point(1, "a"), Point(2, "b"), 3)
    print("read_path", read_path(ln))
    print("member_result", member_result(ln))
    write_scalar(ln)
    print("write_scalar", ln.a.x)
    replace_member(ln)
    print("replace_member", ln.a.x, ln.a.name)
    print("mutate_through_member", mutate_through_member(ln), ln.a.x)
    print("replace_live", replace_live(ln), ln.a.x)
    print("reset_live", reset_live(ln), ln.a.x)
    other = Line(Point(5, "e"), Point(6, "f"), 0)
    print("alias_external", alias_external(ln, other), other.a.x)
    print("sibling_survives", sibling_survives(ln))
    print("scalar_call_is_not_replacement", scalar_call_is_not_replacement(ln), ln.a.x)
    print("copy_does_not_replace_source", copy_does_not_replace_source(ln))
    print("copy_then_replace", copy_then_replace(ln), ln.a.x)
    print("name_then_replace", name_then_replace(ln), ln.a.name)
    f = Frame(build(5), 1)
    print("read_deep", read_deep(f))
    print("replace_ancestor", replace_ancestor(f), f.line.a.x)
    print("member_receiver", member_receiver(f), f.line.a.x)
    print("build_frame", build_frame(7))
    adopt(f, build(8))
    print("adopt", f.line.a.x)
    print("keep_and_store", keep_and_store(f, build(9)), f.line.a.x)
    print("owned_local_path", owned_local_path())
    print("composed", composed(2, "xy"))
    print("hand_over_callers", hand_over_callers())
    print("adopt_live", adopt_live())
    ln.swap()
    print("swap", ln.a.x, ln.b.x)


main()
