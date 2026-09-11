# The non-firing half of the alias-rebind clobber rule: every section here
# rebinds a reference local while a loan of it is live, and every one of them
# is CORRECT -- the loan reads the object it was given, so no warning may
# appear. Each reference section mutates through the loan AFTER the rebind and
# reads the value back through the other handle, so a silent copy would show
# up as a value difference rather than passing parity-blind.
from tpy import Int32, Own, copy, take_ptr
from tplib.rc import Rc


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def bump(self) -> None:
        self.x += 100


# sync FIRST rebind: the loan sits in the block-scoped init storage, which the
# rebind slot does not overlap.
def sync_first_section() -> None:
    p = Point(1)
    alias = p
    p = Point(50)  # tpyc: ok
    alias.bump()
    print("sync_first:", alias.x, p.x)


# loan taken before the loop, rebind inside it: same init/slot split, and the
# body binds no loan of its own for the next iteration to clobber.
def rebind_in_loop_section() -> None:
    p = Point(2)
    alias = p
    i = 0
    while i < 3:
        p = Point(i)  # tpyc: ok
        i += 1
    alias.bump()
    print("rebind_in_loop:", alias.x, p.x)


# sibling branch arms: the then-arm's rebind moves `p` into the slot on ITS
# path only, so the else-arm's loan still sits in the init storage and the
# else-arm's own rebind (its first) does not reach it.
def branch_arms_section(c: bool) -> None:
    p = Point(15)
    if c:
        p = Point(16)
        print("branch_arms_then:", p.x)
    else:
        alias = p
        p = Point(50)  # tpyc: ok
        alias.bump()
        print("branch_arms_else:", alias.x, p.x)


# the loan is dead at the rebind, so nothing observes the clobber.
def dead_alias_section() -> None:
    p = Point(3)
    p = Point(4)
    alias = p
    print("dead_alias_pre:", alias.x)
    p = Point(50)  # tpyc: ok
    print("dead_alias:", p.x)


# `None` stores a null handle; the object the loan holds is untouched.
def none_rebind_section() -> None:
    p: Point | None = Point(5)
    p = Point(6)
    alias = p
    p = None  # tpyc: ok
    alias.bump()
    print("none_rebind:", alias.x, p is None)


# a container insert copies at the boundary (intended here -- the mutation
# below proves it), so `xs` holds no loan of `p` for the rebind to clobber.
def container_insert_section() -> None:
    p = Point(7)
    xs: list[Point] = []
    xs.append(copy(p))
    p.bump()
    p = Point(50)  # tpyc: ok
    print("container_insert:", xs[0].x, p.x)


# rebinding the Ptr LOCAL (a value type) copies a pointer; it clobbers nothing.
def rebound_ptr_section() -> None:
    a = Point(8)
    b = Point(9)
    q = take_ptr(a)
    q = take_ptr(b)  # tpyc: ok
    q.x += 100
    print("rebound_ptr:", a.x, b.x)


# hatch 1: copy() gives the loan an independent object.
def hatch_copy_section() -> None:
    p = Point(10)
    p = Point(11)
    held = copy(p)
    p = Point(50)  # tpyc: ok
    held.bump()
    print("hatch_copy:", held.x, p.x)


# hatch 2: Rc keeps identity AND refcount, so the rebind drops one handle only.
def hatch_rc_section() -> None:
    r = Rc.new(Point(12))
    shared = r.clone()
    r = Rc.new(Point(50))  # tpyc: ok
    shared.bump()
    print("hatch_rc:", shared.x, r.x)


# hatch 3: a fresh name for the new value -- allocation-free, semantics-preserving.
def hatch_fresh_name_section() -> None:
    p = Point(13)
    p = Point(14)
    alias = p
    q = Point(50)
    alias.bump()
    print("hatch_fresh_name:", alias.x, p.x, q.x)


def main() -> None:
    sync_first_section()
    rebind_in_loop_section()
    branch_arms_section(True)
    branch_arms_section(False)
    dead_alias_section()
    none_rebind_section()
    container_insert_section()
    rebound_ptr_section()
    hatch_copy_section()
    hatch_rc_section()
    hatch_fresh_name_section()


main()
