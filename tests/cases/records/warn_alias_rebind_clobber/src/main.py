# The one alias clobber the slot model cannot avoid: a loan taken from a
# rebind site's OWN storage that is still live when that site runs again on
# the next iteration -- one slot per site cannot hold two iterations' objects.
# The rebind warns; `copy(p)` or a fresh name for the new value is the fix.
# The printed values are the WRONG ones the warning announces (CPython keeps
# the object the loan was given) -- hence no_cpython.txt.
from tpy import int32, take_ptr


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


# `q` is re-taken of `p` AFTER the in-loop rebind and read before that on the
# next iteration, so the rebind overwrites what `q` points at.
def loop_carried_section() -> None:
    other = Point(21)
    p = Point(22)
    q = take_ptr(other)
    i = 0
    while i < 2:
        p = Point(i)  # tpyc: warning(/will not keep the object it was given/)
        print("loop_carried_iter:", q.x)
        q = take_ptr(p)
        i += 1
    print("loop_carried:", q.x, p.x)


# the `for` spelling
def for_carried_section() -> None:
    p = Point(30)
    q = take_ptr(p)
    for i in range(2):
        p = Point(i)  # tpyc: warning(/will not keep the object it was given/)
        print("for_carried_iter:", q.x)
        q = take_ptr(p)
    print("for_carried:", q.x, p.x)


# The rebind site is reached with storage it does not own on the first
# iteration (`p = xs[0]`), so it stays OWN -- but its own slot is still
# reused on the next iteration while `q` reads it.
def foreign_origin_section(xs: list[Point]) -> None:
    p = xs[0]
    q = take_ptr(p)
    for i in range(2):
        p = Point(i)  # tpyc: warning(/will not keep the object it was given/)
        print("foreign_origin_iter:", q.x)
        q = take_ptr(p)
    print("foreign_origin:", q.x, p.x)


# The loop body's FIRST bind of `p` (hoisted before the loop for the read
# after it) runs again on the next iteration and reuses its slot the same way.
def hoisted_body_bind_section() -> None:
    other = Point(41)
    q = take_ptr(other)
    for i in range(2):
        p = Point(i)  # tpyc: warning(/will not keep the object it was given/)
        print("hoisted_body_bind_iter:", q.x)
        q = take_ptr(p)
    print("hoisted_body_bind:", q.x, p.x)


# Two holders of the body-bound `p`: the scope-escape check warns for
# `saved` at its alias site, and the rebind still warns for `other`, which
# keeps the object of ONE iteration and reads the site's slot after that.
def two_alias_section() -> None:
    saved = Point(50)
    for i in range(3):
        p = Point(i)  # tpyc: warning(/'other' will not keep the object/)
        saved = p  # tpyc: warning(/'saved' will not keep the object/)
        if i == 0:
            other = p
    other.x += 100
    print("two_alias:", saved.x, other.x)


def main() -> None:
    loop_carried_section()
    for_carried_section()
    foreign_origin_section([Point(40)])
    hoisted_body_bind_section()
    two_alias_section()


main()
