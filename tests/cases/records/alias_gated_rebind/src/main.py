# The alias-gated slot model: an rvalue rebind of a reference local writes IN
# PLACE when nothing aliases its current object (the superseded object drops
# at the rebind, as under CPython) and takes storage of its OWN when a live
# loan still points at it (the loan keeps the old object). One section per
# position and loan shape; every section mutates through the loan AFTER the
# rebind and reads both handles, and the Noisy sections print the drop order.
import asyncio
from tpy import int32, Own, Ptr, take_ptr, error_return, ReturnException
from typing import Iterator


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def bump(self) -> None:
        self.x += 100


class Holder:
    inner: Point

    def __init__(self, inner: Own[Point]) -> None:
        self.inner = inner

    def peek(self) -> Point:
        return self.inner


# `armed` lets a section silence the drops that happen at scope end, where C++
# (reverse declaration order) and CPython (variable order) disagree; only the
# drops AT a rebind are the subject.
class Noisy:
    name: str
    armed: bool

    def __init__(self, name: str) -> None:
        self.name = name
        self.armed = True

    def __del__(self) -> None:
        if self.armed:
            print("drop", self.name)


class Fail(Exception, ReturnException):
    pass


class Wrap:
    p: Ptr[Point]

    def __init__(self, p: Ptr[Point]) -> None:
        self.p = p


class Walker:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def walk(self) -> Iterator[int32]:
        yield self.x
        yield self.x


# free function, whole-name alias: the alias is live, so the rebind owns
def free_alias() -> None:
    p = Point(1)
    alias = p
    p = Point(50)  # tpyc: ok
    alias.bump()
    print("free_alias:", alias.x, p.x)


# free function, no alias: in place, each superseded object drops at the rebind
def free_drop() -> None:
    r = Noisy("a")
    r = Noisy("b")  # tpyc: ok
    print("free_drop alive:", r.name)
    r = Noisy("c")
    print("free_drop alive:", r.name)


# the alias was taken after a first rebind: it keeps the first rebind's object
def second_rebind() -> None:
    p = Point(3)
    p = Point(4)
    alias = p
    p = Point(50)  # tpyc: ok
    alias.bump()
    print("second_rebind:", alias.x, p.x)


# a loan INTO the object (field chain) keeps the old object alive as well
def field_chain() -> None:
    h = Holder(Point(5))
    inner = h.inner
    h = Holder(Point(50))  # tpyc: ok
    inner.bump()
    print("field_chain:", inner.x, h.inner.x)


# element loan: the old list lives on, so the element read stays valid
def element_loan() -> None:
    xs = [Point(7)]
    e = xs[0]
    xs = [Point(50)]  # tpyc: ok
    e.bump()
    print("element_loan:", e.x, xs[0].x)


# a Ptr loan, copied to a second name: the copy carries the loan
def ptr_copy() -> None:
    p = Point(9)
    q = take_ptr(p)
    r = q
    p = Point(50)  # tpyc: ok
    r.x += 100
    print("ptr_copy:", r.x, p.x)


# an alias that is never read after the rebind still holds the object, so
# the rebind owns and "d1" drops at scope end, not at the rebind (CPython's
# refcount keeps it too); only "d2" is disarmed, so the scope-end order
# cannot differ
def dead_alias() -> None:
    r = Noisy("d1")
    alias = r
    print("dead_alias:", alias.name)
    r = Noisy("d2")  # tpyc: ok
    print("dead_alias alive:", r.name)
    r.armed = False


# a loan bound before a branch that rebinds on one arm: the post-join rebind
# owns (the alias may still point at the original on the other arm)
def after_branch(c: bool) -> None:
    p = Point(15)
    alias = p
    if c:
        p = Point(16)  # tpyc: ok
    p = Point(50)  # tpyc: ok
    alias.bump()
    print("after_branch:", alias.x, p.x)


# a loan the loop body binds is still held after the loop; the post-loop
# rebind owns, the in-loop one sees a dead holder (rebound before any read)
def after_loop() -> None:
    saved = Point(17)
    p = Point(18)
    for i in range(2):
        p = Point(i)  # tpyc: ok
        saved = p
    p = Point(50)  # tpyc: ok
    saved.bump()
    print("after_loop:", saved.x, p.x)


# a loan bound before the loop, rebinds inside it: every iteration owns
def loan_before_loop() -> None:
    p = Point(20)
    alias = p
    i = 0
    while i < 2:
        p = Point(i)  # tpyc: ok
        i += 1
    alias.bump()
    print("loan_before_loop:", alias.x, p.x)


# a borrow-returning method result is a loan into the object
def borrow_call() -> None:
    h = Holder(Point(16))
    q = h.peek()
    h = Holder(Point(60))  # tpyc: ok
    q.bump()
    print("borrow_call:", q.x, h.inner.x)


# a Ptr passed as a constructor argument leaves through the call: held for
# the rest of the body, so the rebind owns
def ptr_escapes_into_record() -> None:
    p = Point(17)
    w = Wrap(take_ptr(p))
    p = Point(50)  # tpyc: ok
    w.p.x += 100
    print("ptr_escapes:", w.p.x, p.x)


# a generator object holds its receiver: the rebind owns, the generator keeps
# yielding from the object it was made from
def generator_holds_receiver() -> None:
    wk = Walker(18)
    g = wk.walk()
    wk = Walker(50)  # tpyc: ok
    for v in g:
        print("generator_holds_receiver:", v, wk.x)


# constructor body
class Built:
    a: int32
    b: int32

    def __init__(self) -> None:
        p = Point(19)
        alias = p
        p = Point(50)  # tpyc: ok
        alias.bump()
        self.a = alias.x
        self.b = p.x


# method body
class Runner:
    def run(self) -> None:
        p = Point(11)
        alias = p
        p = Point(50)  # tpyc: ok
        alias.bump()
        print("method:", alias.x, p.x)


# generator frame: the alias survives the rebind across a yield (the local
# goes pointer-form over per-site frame fields)
def gen_section() -> Iterator[int32]:
    p = Point(1)
    saved = p
    yield p.x
    p = Point(2)  # tpyc: ok
    saved.bump()
    yield saved.x
    yield p.x


# generator frame, no alias: the object drops at the rebind, not with the frame
def gen_drop() -> Iterator[int32]:
    r = Noisy("g1")
    yield 1
    r = Noisy("g2")  # tpyc: ok
    yield 2


# async frame
async def async_section() -> int32:
    p = Point(2)
    alias = p
    await asyncio.sleep(0)
    p = Point(50)  # tpyc: ok
    alias.bump()
    return alias.x + p.x


# nested def with a local of its own
def nested_def_section() -> None:
    def inner() -> None:
        n = Point(11)
        nalias = n
        n = Point(50)  # tpyc: ok
        nalias.bump()
        print("nested_def:", nalias.x, n.x)

    inner()


# context-manager body
class Guard:
    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def with_body() -> None:
    with Guard():
        p = Point(12)
        alias = p
        p = Point(50)  # tpyc: ok
        alias.bump()
        print("with_body:", alias.x, p.x)


# try / finally
def try_finally() -> None:
    p = Point(13)
    alias = p
    try:
        p = Point(50)  # tpyc: ok
    finally:
        alias.bump()
        print("try_finally:", alias.x, p.x)


# match arm
def match_arm(n: int32) -> None:
    p = Point(14)
    alias = p
    match n:
        case 0:
            p = Point(50)  # tpyc: ok
        case _:
            p = Point(60)  # tpyc: ok
    alias.bump()
    print("match_arm:", alias.x, p.x)


# @error_return body: the unwrap-bound rebind takes the same verdict
@error_return(Fail)
def make_point(v: int32) -> Own[Point]:
    if v < 0:
        raise Fail
    return Point(v)


@error_return(Fail)
def error_return_body() -> int32:
    p = make_point(15)
    alias = p
    p = make_point(50)  # tpyc: ok
    alias.bump()
    return alias.x + p.x


def main() -> None:
    free_alias()
    free_drop()
    second_rebind()
    field_chain()
    element_loan()
    ptr_copy()
    dead_alias()
    after_branch(True)
    after_branch(False)
    after_loop()
    loan_before_loop()
    borrow_call()
    ptr_escapes_into_record()
    generator_holds_receiver()
    b = Built()
    print("constructor:", b.a, b.b)
    Runner().run()
    for got in gen_section():
        print("gen:", got)
    for got in gen_drop():
        print("gen_drop:", got)
    print("async:", asyncio.run(async_section()))
    nested_def_section()
    with_body()
    try_finally()
    match_arm(0)
    match_arm(1)
    try:
        print("error_return:", error_return_body())
    except Fail:
        print("error_return: failed")


main()

# module level: a pointer-slot global rebound under a live alias owns; an
# unaliased rebind of another writes in place
g = Point(12)
galias = g
g = Point(50)  # tpyc: ok
galias.bump()
print("module:", galias.x, g.x)
gr = Noisy("m1")
gr = Noisy("m2")  # tpyc: ok
print("module alive:", gr.name)
gr.armed = False
