# Printing callees summarize as known (print reads its arguments, writes no
# storage), so their callers are analyzed and the argument-alias rule reaches them.
import sys

from tpy import auto_readonly, int32, readonly

counter = 7


# appends through `b` while iterating `a`, printing on every pass; the callers
# pass elements >= 3, so no run appends and the output matches CPython
def two(a: list[int32], b: list[int32]) -> None:  # tpyc: mir(conflict /alias_obligation/)
    for y in a:
        if y < 3:
            b.append(y + 10)
        print("printing two:", y)


# one list twice to a printing callee that may append under its own loop: warns
def alias_call(x: list[int32]) -> None:  # tpyc: mir(conflict /argument_alias/)
    two(x, x)  # tpyc: warning(/passed as both 'a' and 'b'/)


# element-write twin: writes an element, never grows the list
def two_set(a: list[int32], b: list[int32]) -> None:
    for y in a:
        b[0] = 50
        print("printing two_set:", y)


# inverse: an element write does not invalidate the loop: silent
def set_call(x: list[int32]) -> None:  # tpyc: mir(covered)
    two_set(x, x)  # tpyc: ok


# a covered helper forwarding both lists to the printing callee: known, and it
# publishes the callee's no-alias obligation to its own callers
def forward(a: list[int32], b: list[int32]) -> None:  # tpyc: mir(conflict /alias_obligation/) mir_summary(known)
    two(a, b)


# one list twice through the forwarder: warns, as the direct call does
def mid(xs: list[int32]) -> None:  # tpyc: mir(conflict /argument_alias/)
    forward(xs, xs)  # tpyc: warning(/passed as both 'a' and 'b' of 'forward'/)


# the silent twin of `forward`: forwarding to the element-write callee
def forward_set(a: list[int32], b: list[int32]) -> None:  # tpyc: mir(conflict /alias_obligation/) mir_summary(known)
    two_set(a, b)


# one list twice through the element-write forwarder: silent
def fwd_set_call(w: list[int32]) -> None:  # tpyc: mir(covered)
    forward_set(w, w)  # tpyc: ok


# a helper printing a module global: known
def show_global() -> None:  # tpyc: mir(covered) mir_summary(known)
    print("printing global:", counter)


def global_caller() -> None:  # tpyc: mir(covered)
    show_global()


class Loud:
    n: int32

    # a printing constructor: known
    def __init__(self, n: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.n = n
        print("printing init:", n)

    # a printing method: known
    def say(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        print("printing say:", self.n)


def method_caller(n: int32) -> None:  # tpyc: mir(covered)
    o = Loud(n)
    o.say()


class Tally:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    # a printing @auto_readonly method: both clones are known
    @auto_readonly
    def shown(self) -> "Tally":  # tpyc: mir_summary(known)
        print("printing shown:", self.n)
        return self


# the mutable clone, called from a covered caller that writes through the result
def mutable_shown(t: Tally) -> None:  # tpyc: mir(covered)
    d = t.shown()
    d.n = 9


# the readonly clone, called from a covered caller
def readonly_shown(t: readonly[Tally]) -> int32:  # tpyc: mir(covered)
    d = t.shown()
    return d.n


# print forms MIR does not lower keep their body uncovered, and so its callers
def custom_sep(n: int32, sep: str) -> None:  # tpyc: mir(uncovered /unsupported metadata: sep_expr/)
    print("printing custom_sep:", n, sep=sep)


def sep_caller(n: int32) -> None:  # tpyc: mir(uncovered /call needs finalized known summary/)
    custom_sep(n, "|")


# a `file=` sink (stderr is not part of the compared output)
def to_stderr(n: int32) -> None:  # tpyc: mir(uncovered /unsupported metadata: sink_expr/)
    print(n, file=sys.stderr)


def stderr_caller(n: int32) -> None:  # tpyc: mir(uncovered /call needs finalized known summary/)
    to_stderr(n)


class Named:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __str__(self) -> str:
        return "printing Named"


# a record with a user `__str__` printed
def show_named(r: Named) -> None:  # tpyc: mir(uncovered /unsupported expression form/)
    print(r)


def named_caller() -> None:  # tpyc: mir(uncovered /call needs finalized known summary/)
    show_named(Named(1))


class Box:
    items: list[Tally]

    def __init__(self) -> None:
        self.items = [Tally(1), Tally(2)]

    @auto_readonly
    def last(self) -> Tally:
        return self.items[-1]


# A record passed twice: growing `h.items` may move the element `ro.last()` returned.
def ro_two(ro: readonly[Box], h: Box, grow: bool) -> None:
    a = ro.last()
    if grow:
        h.items.append(Tally(9))
    print("printing ro_two:", a.n)


def ro_two_caller() -> None:  # tpyc: mir(conflict /argument_alias/)
    b = Box()
    ro_two(b, b, False)  # tpyc: warning(/'ro_two' modifies through parameter 'h' while it still uses 'ro'/)
    b.items.append(Tally(5))
    print("printing ro_two_caller:", len(b.items))


def main() -> None:
    x = [3, 4]
    alias_call(x)
    x.append(5)
    print("printing alias_call:", len(x), x[2])
    y = [1, 2]
    set_call(y)
    print("printing set_call:", y[0], y[1])
    z = [6]
    mid(z)
    z.append(8)
    print("printing mid:", len(z), z[1])
    w = [1, 2]
    fwd_set_call(w)
    w.append(9)
    print("printing fwd_set_call:", len(w), w[0], w[2])
    global_caller()
    method_caller(4)
    t = Tally(1)
    mutable_shown(t)
    n = readonly_shown(t)
    print("printing mutable_shown:", t.n, n)
    sep_caller(1)
    stderr_caller(2)
    named_caller()
    ro_two_caller()
