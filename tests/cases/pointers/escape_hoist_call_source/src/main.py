# A call that hands back a reference into a loop-local (a method, a free
# function, a chain, a ternary of calls, an Optional return) escapes the loop
# exactly like the field read it wraps: the local is hoisted and the bind warns.
from typing import Iterator, Optional
from tpy import int32, Own, copy, nocopy


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Mid:
    r: Rec

    def __init__(self, t: int32) -> None:
        self.r = Rec(t)

    def rec_m(self) -> Rec:
        return self.r


class B:
    m: Rec
    mid: Mid
    o: Optional[Rec]

    def __init__(self, tag: int32) -> None:
        self.m = Rec(tag)
        self.mid = Mid(tag)
        self.o = Rec(tag)

    @property
    def opt(self) -> Optional[Rec]:
        return self.o

    def rec_m(self) -> Rec:
        return self.m

    def mid_m(self) -> Mid:
        return self.mid

    def at(self, i: int32) -> Rec:
        return self.m

    def opt_m(self) -> Optional[Rec]:
        return self.o

    # returns its borrow through a local alias
    def alias_m(self) -> Rec:
        t = self.m
        return t

    # returns a borrow-returning call made on a FIELD of self
    def mid_rec(self) -> Rec:
        return self.mid.rec_m()

    def own_m(self) -> Own[Rec]:
        return Rec(self.m.x + 50)

    def val_m(self) -> int32:
        return self.m.x


@nocopy
class Sealed:
    m: Rec

    def __init__(self, tag: int32) -> None:
        self.m = Rec(tag)

    def rec_m(self) -> Rec:
        return self.m


class Shelf:
    bs: list[B]

    def __init__(self) -> None:
        self.bs = [B(1), B(2), B(3)]

    def bs_m(self) -> list[B]:
        return self.bs


def first(b: B) -> Rec:
    return b.m


def second_of(a: B, b: B) -> Rec:
    return b.m


def either(a: B, b: B, first_one: bool) -> Rec:
    if first_one:
        return a.m
    return b.m


# a re-seated alias lends every source it was seated on
def reseated(b: B, k: B, pick_b: bool) -> Rec:
    t = k.m
    if pick_b:
        t = b.m
    return t


# the alias is of `k`: `b` is only read
def alias_of_other(b: B, k: B) -> Rec:
    t = k.m
    t.x += b.m.x
    return t


# method: the receiver is the loop-local
def method_section() -> None:
    holder = Rec(0)
    for i in range(3):
        b = B(i)
        holder = b.rec_m()  # tpyc: warning(/'holder' will not keep the object it was given -- 'b' is rebound/)
        holder.x += 10
        print("method in-loop:", b.m.x)
    holder.x += 100
    print("method:", holder.x)


# method taking an argument: still the receiver's storage
def method_arg_section() -> None:
    holder = Rec(0)
    for i in range(3):
        b = B(i)
        holder = b.at(i)  # tpyc: warning(/use copy\(\) on the assigned value/)
        holder.x += 10
        print("method_arg in-loop:", b.m.x)
    holder.x += 100
    print("method_arg:", holder.x)


# free function: the borrowed ARGUMENT is the loop-local
def free_function_section() -> None:
    holder = Rec(0)
    for i in range(3):
        b = B(i)
        holder = first(b)  # tpyc: warning(/'b' is rebound on each iteration/)
        holder.x += 10
        print("free in-loop:", b.m.x)
    holder.x += 100
    print("free:", holder.x)


# chain: a method off a field of the loop-local
def chain_section() -> None:
    holder = Rec(0)
    for i in range(3):
        b = B(i)
        holder = b.mid.rec_m()  # tpyc: warning(/'b' is rebound on each iteration/)
        holder.x += 10
        print("chain in-loop:", b.mid.r.x)
    holder.x += 100
    print("chain:", holder.x)


# the borrowed argument is the SECOND one: only `b` escapes, not `k`
def second_arg_section() -> None:
    k = B(9)
    holder = Rec(0)
    for i in range(3):
        b = B(i)
        holder = second_of(k, b)  # tpyc: warning(/'b' is rebound on each iteration/)
        holder.x += 10
        print("second_arg in-loop:", b.m.x, k.m.x)
    holder.x += 100
    print("second_arg:", holder.x)


# a method off a reference-returning method: the root is still `b`. The first
# warning on the line is BUGS.md#ref-returning-receiver-call-called-temporary.
def chain_of_calls_section() -> None:
    holder = Rec(0)
    for i in range(3):
        b = B(i)
        holder = b.mid_m().rec_m()  # tpyc: warning(/'b' is rebound on each iteration/)
        holder.x += 10
        print("chain_of_calls in-loop:", b.mid.r.x)
    holder.x += 100
    print("chain_of_calls:", holder.x)


# ternary of two calls: only the loop-local arm's root is hoisted
def ternary_section() -> None:
    k = B(9)
    holder = Rec(0)
    for i in range(3):
        b = B(i)
        holder = b.rec_m() if i > 0 else k.rec_m()  # tpyc: warning(/'b' is rebound on each iteration/)
        holder.x += 10
        print("ternary in-loop:", b.m.x, k.m.x)
    holder.x += 100
    print("ternary:", holder.x)


# Optional return: the pointer form borrows the receiver too
def optional_section() -> None:
    k = B(9)
    v = k.opt
    for i in range(3):
        b = B(i)
        v = b.opt_m()  # tpyc: warning(/'v' will not keep the object it was given/)
        if v is not None:
            v.x += 10
        o = b.o
        if o is not None:
            print("optional in-loop:", o.x)
    if v is not None:
        v.x += 100
        print("optional:", v.x)


# while loop and nested loop
def while_nested_section() -> None:
    holder = Rec(0)
    i = 0
    while i < 3:
        b = B(i)
        holder = b.rec_m()  # tpyc: warning(/will not keep the object it was given/)
        i += 1
    print("while:", holder.x)
    for j in range(2):
        c = B(j + 20)
        for n in range(2):
            holder = first(c)  # tpyc: warning(/'c' is rebound on each iteration/)
    print("nested:", holder.x)


# method body position
class Runner:
    last: int32

    def __init__(self) -> None:
        self.last = 0

    def run(self) -> None:
        holder = Rec(0)
        for i in range(3):
            b = B(i)
            holder = b.rec_m()  # tpyc: warning(/will not keep the object it was given/)
        self.last = holder.x


# a call lending from TWO loop-locals: each is judged, both are hoisted
def two_roots_section() -> None:
    holder = Rec(0)
    for i in range(3):
        p = B(i)
        q = B(i + 10)
        holder = either(p, q, i == 1)  # tpyc: warning(/'p' is rebound on each iteration/) warning(/'q' is rebound on each iteration/)
        holder.x += 100
        print("two roots in-loop:", p.m.x, q.m.x)
    print("two roots:", holder.x)


# a @nocopy source has no copy() remedy to name
def nocopy_section() -> None:
    holder = Rec(0)
    for i in range(3):
        s = Sealed(i)
        holder = s.rec_m()  # tpyc: warning(/'s' cannot be copied/)
        holder.x += 10
        print("nocopy in-loop:", s.m.x)
    print("nocopy:", holder.x)


# an alias or a for-each var behind a call is not judged at all: its own scope
# says nothing about the storage it refers to, and here that storage outlives
# the target, so these everyday shapes must keep compiling and aliasing
# (the dangling twin is BUGS.md#call-source-escape-through-alias-unchecked)
def outliving_storage_section(src: B) -> None:
    rows = [B(1), B(2), B(3)]
    shelf = Shelf()
    by_key = {1: B(4), 2: B(5)}
    holder = Rec(0)
    for i in range(3):
        b = src
        holder = b.rec_m()  # tpyc: ok
        holder.x += 1
    print("alias of param:", holder.x, src.m.x)
    for i in range(3):
        r = rows[i]
        holder = first(r)  # tpyc: ok
        holder.x += 10
    print("alias of outer element:", holder.x, rows[2].m.x)
    for it in shelf.bs_m():
        holder = it.rec_m()  # tpyc: ok
        holder.x += 10
    print("for-each over a returned list:", holder.x, shelf.bs[2].m.x)
    for v in by_key.values():
        holder = v.rec_m()  # tpyc: ok
        holder.x += 10
    print("for-each over values():", holder.x)


# the callee hands its borrow back through a local alias, a re-seated alias
# or a call on one of its own fields: the fact is the same as `return b.m`
def callee_shape_section() -> None:
    k = B(9)
    holder = Rec(0)
    for i in range(3):
        b = B(i)
        holder = b.alias_m()  # tpyc: warning(/'b' is rebound on each iteration/)
        holder.x += 10
        print("callee alias in-loop:", b.m.x)
    print("callee alias:", holder.x)
    for i in range(3):
        c = B(i)
        holder = c.mid_rec()  # tpyc: warning(/'c' is rebound on each iteration/)
        holder.x += 10
        print("callee field-call in-loop:", c.mid.r.x)
    print("callee field-call:", holder.x)
    for i in range(3):
        d = B(i)
        holder = reseated(d, k, i > 0)  # tpyc: warning(/'d' is rebound on each iteration/)
        holder.x += 10
        print("callee reseated in-loop:", d.m.x, k.m.x)
    print("callee reseated:", holder.x)
    for i in range(3):
        e = B(i)
        holder = alias_of_other(e, k)  # tpyc: ok
    print("callee other:", holder.x, k.m.x)


# nested def: the closure body gives the escaping local its slot too
def nested_def_section() -> None:
    def inner() -> None:
        holder = Rec(0)
        for i in range(3):
            b = B(i)
            holder = b.rec_m()  # tpyc: warning(/'b' is rebound on each iteration/)
            holder.x += 10
            print("nested in-loop:", b.m.x)
        holder.x += 100
        print("nested:", holder.x)
    inner()


# forward reference: the callee's body is analyzed AFTER this one, so the
# verdict waits for its borrow fact and does not depend on definition order
class Forward:
    q: Rec

    def __init__(self) -> None:
        self.q = Rec(40)

    def run(self) -> None:
        holder = Rec(0)
        for i in range(3):
            t = B(i)
            # borrows `self.q`, never `t`: nothing to say
            holder = self.own_q(t)  # tpyc: ok
            holder.x += 1
        print("forward sibling:", holder.x, self.q.x)
        for i in range(3):
            b = B(i)
            holder = later(b)  # tpyc: warning(/'b' is rebound on each iteration/)
            holder.x += 10
            print("forward in-loop:", b.m.x)
        holder.x += 100
        print("forward:", holder.x)

    def own_q(self, t: B) -> Rec:
        return self.q


def later(b: B) -> Rec:
    return b.m


# the wrapper aliases the result of a helper declared BELOW it, which lends
# only `k`: an unanalyzed callee's guess must not be recorded as the fact
def wraps_later(k: B, c: B) -> Rec:
    t = lends_first(k, c)
    return t


def lends_first(k: B, c: B) -> Rec:
    k.m.x += c.m.x
    return k.m


def wrapper_of_later_section() -> None:
    k = B(9)
    holder = Rec(0)
    for i in range(3):
        c = B(i)
        holder = wraps_later(k, c)  # tpyc: ok
        holder.x += 10
    print("wrapper of later:", holder.x, k.m.x)


# a generator's nested def with an ordinary branch pre-declaration is not an
# escape hoist and keeps compiling
def gen_host() -> Iterator[int32]:
    def inner() -> int32:
        try:
            r = Rec(5)
        finally:
            pass
        return r.x
    yield inner()


# the explicit copy is the escape hatch
def copy_section() -> None:
    holder = Rec(0)
    for i in range(3):
        b = B(i)
        holder = copy(b.rec_m())  # tpyc: ok
        holder.x += 10
        print("copy in-loop:", b.m.x)
    holder.x += 100
    print("copy:", holder.x)


# inverse: nothing here lends loop-local storage past the loop
def inverse_section(p: B) -> None:
    outer = B(7)
    holder = Rec(0)
    total = 0
    for i in range(3):
        b = B(i)
        holder = outer.rec_m()  # tpyc: ok
        holder = first(p)  # tpyc: ok
        holder = b.own_m()  # tpyc: ok
        total += b.val_m()  # tpyc: ok
        inner = b.rec_m()  # tpyc: ok
        inner.x += 1
        total += inner.x
    holder.x += 100
    print("inverse:", holder.x, total)


def main() -> None:
    method_section()
    method_arg_section()
    free_function_section()
    chain_section()
    second_arg_section()
    chain_of_calls_section()
    ternary_section()
    optional_section()
    while_nested_section()
    r = Runner()
    r.run()
    print("runner:", r.last)
    two_roots_section()
    nocopy_section()
    outliving_storage_section(B(30))
    callee_shape_section()
    nested_def_section()
    Forward().run()
    wrapper_of_later_section()
    for v in gen_host():
        print("gen-hosted nested def:", v)
    copy_section()
    inverse_section(B(3))



# module level: a call source takes the field spelling's verdict here too
# (the wording is BUGS.md#module-level-escape-warning-names-a-loop)
TOP = B(60)
TOP_HOLD: Rec = TOP.rec_m()  # tpyc: warning(/'TOP_HOLD' will not keep the object it was given/)
TOP_HOLD.x += 1
print("module level:", TOP_HOLD.x, TOP.m.x)

main()
