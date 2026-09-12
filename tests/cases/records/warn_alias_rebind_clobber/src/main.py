# Rebinding a reference local while a live loan still points at the object it
# held hands the loan the NEW object. One section per position that holds ONE
# object generation (frame generator, async def, module level, nested def with
# a non-colliding name) plus the sync SECOND rebind, and one per loan form that
# the rule sees (name, field chain, element, Ptr).
#
# The printed values are the WRONG ones the warning announces
# (BUGS.md#resumable-alias-identity) -- hence no_cpython.txt.
import asyncio
from tpy import int32, Own, take_ptr
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


# frame generator: one frame field per name, so the FIRST rebind clobbers.
def gen_section() -> Iterator[int32]:
    p = Point(1)
    alias = p
    p = Point(50)  # tpyc: warning(/will not keep the object it was given/)
    alias.bump()
    yield alias.x
    yield p.x


# async def: same resumable frame, same single generation.
async def async_section() -> int32:
    p = Point(2)
    alias = p
    p = Point(50)  # tpyc: warning(/will not keep the object it was given/)
    alias.bump()
    return alias.x


# sync body: the SECOND rebind writes the slot the alias was taken from.
def sync_second_section() -> None:
    p = Point(3)
    p = Point(4)
    alias = p
    p = Point(50)  # tpyc: warning(/will not keep the object it was given/)
    alias.bump()
    print("sync_second:", alias.x, p.x)


# field-chain loan: `inner` points into h's object, which the rebind replaces.
def field_chain_section() -> None:
    h = Holder(Point(5))
    h = Holder(Point(6))
    inner = h.inner
    h = Holder(Point(50))  # tpyc: warning(/will not keep the object it was given/)
    inner.bump()
    print("field_chain:", inner.x, h.inner.x)


# element loan: `e` points into the list the rebind replaces. Unlike the other
# sections the old storage is a heap buffer the rebind FREES, so reading `e`
# afterwards is a use-after-free with no stable value to pin -- the read is
# kept live for the analysis but never executed (`run` is False). The dangling
# read is the separate defect BUGS.md#container-rebind-frees-element-loan; the
# warning below announces the clobber, not the free.
def element_section(run: bool) -> None:
    xs = [Point(7)]
    xs = [Point(8)]
    e = xs[0]
    xs = [Point(50)]  # tpyc: warning(/will not keep the object it was given/)
    if run:
        e.bump()
        print("element-unreachable:", e.x)
    print("element:", xs[0].x)


# Ptr loan: a value-typed holder still borrows the storage.
def ptr_section() -> None:
    p = Point(9)
    p = Point(10)
    q = take_ptr(p)
    p = Point(50)  # tpyc: warning(/will not keep the object it was given/)
    q.x += 100
    print("ptr:", q.x, p.x)


# nested def whose local does not collide with an enclosing rebound name:
# the lowering reserves no slot for it, so the FIRST rebind clobbers.
def nested_def_section() -> None:
    def inner() -> None:
        n = Point(11)
        nalias = n
        n = Point(50)  # tpyc: warning(/will not keep the object it was given/)
        nalias.bump()
        print("nested_def:", nalias.x, n.x)

    inner()


# a rebind on ONE arm of a branch still moves the value into the slot, so a
# loan taken after the join sits in the slot the next rebind writes.
def after_branch_section(c: bool) -> None:
    p = Point(15)
    if c:
        p = Point(16)
    alias = p
    p = Point(50)  # tpyc: warning(/will not keep the object it was given/)
    alias.bump()
    print("after_branch:", alias.x, p.x)


# a loan the LOOP BODY binds is still held after the last iteration, so the
# post-loop rebind clobbers it (the annotation is on the post-loop line).
def after_loop_section() -> None:
    saved = Point(17)
    p = Point(18)
    for i in range(2):
        p = Point(i)
        saved = p
    p = Point(50)  # tpyc: warning(/will not keep the object it was given/)
    saved.bump()
    print("after_loop:", saved.x, p.x)


# the HOLDER is first bound inside the body: TPy locals are function-scoped, so
# its loan is still live at the post-loop rebind (a record-typed holder hits
# the codegen reject stmt.for_each:foreach.hoist_type; a Ptr one compiles).
def body_local_holder_section() -> None:
    p = Point(24)
    p = Point(25)
    for i in range(2):
        p = Point(i)
        loan = take_ptr(p)
    p = Point(50)  # tpyc: warning(/will not keep the object it was given/)
    loan.bump()
    print("body_local_holder:", loan.x, p.x)


# the `while` spelling of the same carry: sema walks the body once either way.
def after_while_section() -> None:
    saved = Point(19)
    p = Point(20)
    i = 0
    while i < 2:
        p = Point(i)
        saved = p
        i += 1
    p = Point(50)  # tpyc: warning(/will not keep the object it was given/)
    saved.bump()
    print("after_while:", saved.x, p.x)


# the loop-carried leg: `q` is bound BEFORE the loop to another object and
# re-taken of `p` after the in-loop rebind, so only the body pre-scan can see
# that the next iteration's rebind clobbers it.
def loop_carried_section() -> None:
    other = Point(21)
    p = Point(22)
    p = Point(23)
    q = take_ptr(other)
    i = 0
    while i < 2:
        p = Point(i)  # tpyc: warning(/will not keep the object it was given/)
        print("loop_carried_iter:", q.x)
        q = take_ptr(p)
        i += 1
    print("loop_carried:", q.x, p.x)


def main() -> None:
    for got in gen_section():
        print("gen:", got)
    print("async:", asyncio.run(async_section()))
    sync_second_section()
    field_chain_section()
    element_section(False)
    ptr_section()
    nested_def_section()
    after_branch_section(True)
    after_loop_section()
    body_local_holder_section()
    after_while_section()
    loop_carried_section()


main()

# module level: no rebind slot at all, so the FIRST rebind clobbers.
g = Point(12)
galias = g
g = Point(50)  # tpyc: warning(/will not keep the object it was given/)
galias.bump()
print("module:", galias.x, g.x)
