# The one alias clobber the slot model cannot avoid: a loan taken from a
# rebind site's OWN storage that is still live when that site runs again on
# the next iteration -- one slot per site cannot hold two iterations' objects.
# The rebind warns; `copy(p)` or a fresh name for the new value is the fix.
# The printed values are the WRONG ones the warning announces (CPython keeps
# the object the loan was given) -- hence no_cpython.txt.
from typing import Iterator

from tpy import Own, StrView, int32, take_ptr


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


# Two holders of the body-bound `p`: the scope-escape check warns at each
# alias site -- `saved`, taken on every iteration, and `other`, taken on one
# of them and then reading the site's slot after the loop.
def two_alias_section() -> None:
    saved = Point(50)
    # Seeded before the loop: the alias below is taken on ONE body path, so
    # without this `other` would not be assigned after the loop.
    other = Point(60)
    for i in range(3):
        p = Point(i)
        saved = p  # tpyc: warning(/'saved' will not keep the object/)
        if i == 0:
            other = p  # tpyc: warning(/'other' will not keep the object/)
    other.x += 100
    print("two_alias:", saved.x, other.x)


# The holder is a plain ALIAS of `p` (not a loan pointing INTO it), and it is
# a body-first local at the loop's own depth, so the scope-escape check has
# nothing to say and the rebind site is what warns -- with the remedy only the
# alias kind has, `copy(p)`.
def alias_kind_section() -> None:
    p = Point(70)
    for i in range(3):
        held = p
        print("alias_kind_iter:", held.x)
        p = Point(i)  # tpyc: warning(/share its storage/)
    held.x += 100
    print("alias_kind:", held.x, p.x)


# `p` is bound at two sites in the loop body, each with a slot of its own:
# `held` takes the second site's object, and the second site rebuilds that
# slot on the next pass. The one-site rebind's warning, at the second site
# (BUGS.md#loop-rebind-site-slot-reuse-warned).
def loop_two_site_section() -> None:
    p = Point(0)
    p = Point(1)
    for i in range(2):
        held = p
        p = Point(3 + i)  # tpyc: ok
        p = Point(5 + i)  # tpyc: warning(/'held' will not keep the object it was given/)
        if i == 1:
            held.x += 100
            print("loop_two_site:", held.x, p.x)


# The same two sites with no alias read after them: no warning.
def loop_two_site_silent_section() -> None:
    p = Point(0)
    p = Point(1)
    for i in range(2):
        p = Point(3 + i)  # tpyc: ok
        p = Point(5 + i)  # tpyc: ok
        print("loop_two_site_silent:", p.x)


# Three sites, the alias taken after the second: each site has a slot of its
# own, and `held` is rebound before the second site runs again, so nothing it
# still reads is rebuilt: no warning, CPython's values.
def loop_three_site_section() -> None:
    held = Point(-1)
    later = Point(-2)
    p = Point(0)
    for i in range(2):
        p = Point(10 + i)  # tpyc: ok
        if i == 1:
            print("loop_three_site:", held.x, later.x)
        p = Point(20 + i)
        held = p
        p = Point(30 + i)
        later = p
    print("loop_three_site:", held.x, later.x, p.x)


# The two sites in a generator body: the same warning at the second site
# (BUGS.md#loop-rebind-site-slot-reuse-warned).
def loop_two_site_gen() -> Iterator[int32]:
    p = Point(0)
    p = Point(1)
    held = p
    for i in range(2):
        held = p
        p = Point(3 + i)  # tpyc: ok
        yield p.x
        p = Point(5 + i)  # tpyc: warning(/'held' will not keep the object it was given/)
    held.x += 100
    yield held.x


def mk2(i: int32) -> tuple[Own[Point], Own[Point]]:
    return (Point(i), Point(i + 1))


# A loop-body tuple unpack is a site like an assignment: its first bind of
# `a` runs again on the next pass while `q` may still read that slot
# (BUGS.md#loop-rebind-site-slot-reuse-warned). `q` is read only while it
# points at `other`: the object it is given dies with its pass.
def loop_unpack_first_bind_section() -> None:
    other = Point(80)
    q = take_ptr(other)
    for i in range(2):
        a, b = mk2(10 * i)  # tpyc: warning(/'q' will not keep the object it was given/)
        if i == 0:
            print("loop_unpack_first_bind_iter:", q.x, b.x)
        q = take_ptr(a)
        print("loop_unpack_first_bind:", a.x, b.x)


def mk(i: int32) -> Own[Point]:
    return Point(i)


# A loop-body `match` capture of a fresh subject is a site like an
# assignment: it binds `p` again on the next pass while `q` may still read
# that slot (BUGS.md#loop-rebind-site-slot-reuse-warned). `q` is read only
# while it points at `other`: the object it is given dies with its pass.
def loop_capture_section() -> None:
    other = Point(90)
    q = take_ptr(other)
    for i in range(2):
        match mk(10 * i + 1):  # tpyc: warning(/'q' will not keep the object it was given/)
            case Point() as p:
                if i == 0:
                    print("loop_capture_iter:", q.x)
                q = take_ptr(p)
                print("loop_capture:", p.x)


# The same capture with no holder carried to the next pass: no warning.
def loop_capture_quiet_section() -> None:
    for i in range(2):
        match mk(10 * i + 1):  # tpyc: ok
            case Point() as p:
                q = take_ptr(p)
                print("loop_capture_quiet:", q.x, p.x)


# The unpack rebinding a generator local: `keep` takes the first pass's
# object and the unpack rebuilds that slot on the next pass, warned as the
# assignment twin is (BUGS.md#loop-rebind-site-slot-reuse-warned).
def loop_unpack_site_gen() -> Iterator[int32]:
    a = Point(1)
    keep = a
    yield a.x
    for i in range(3):
        a, b = mk2(10 * i)  # tpyc: warning(/'keep' will not keep the object it was given/)
        if i == 0:
            keep = a
        yield a.x + b.x
    yield keep.x


# The alias is read only before the two sites run: nothing it still reads is
# rebuilt, so no warning.
def loop_two_site_read_before_section() -> None:
    p = Point(0)
    p = Point(1)
    for i in range(2):
        held = p
        print("loop_two_site_read_before:", held.x)
        p = Point(3 + i)  # tpyc: ok
        p = Point(5 + i)  # tpyc: ok
    print("loop_two_site_read_before:", p.x)


def mk_pair(i: int32) -> tuple[Own[Point], int32]:
    return (Point(i), i)


# A loop-body unpack target is the declaration's twin: `kept`, taken on the
# first pass only, shares the target's slot, so after the loop it reads the
# last pass's object -- the warning is true here.
def unpack_kept_first_pass_section() -> None:
    kept = Point(-1)
    for i in range(3):
        p, k = mk_pair(i)
        if i == 0:
            kept = p  # tpyc: warning(/'kept' will not keep the object/)
    kept.x += 100
    print("unpack_kept_first_pass:", kept.x)


class Named:
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


def ident_view(s: StrView) -> StrView:
    return s


# A view returned through a call lends the view constructor's argument, so
# `v` reads the loop site's object, which the next pass rebuilds in place
# (short names: the rebuilt string stays in the object's own buffer).
def view_through_call_section() -> None:
    n = Named("n0")
    v: StrView = ident_view(StrView(n.s))
    for i in range(2):
        n = Named("n" + str(i + 1))  # tpyc: warning(/'v' will not keep the object it was given/)
        print("view_through_call_iter:", v)
        v = ident_view(StrView(n.s))
    print("view_through_call:", v, n.s)


def main() -> None:
    loop_carried_section()
    for_carried_section()
    foreign_origin_section([Point(40)])
    hoisted_body_bind_section()
    two_alias_section()
    alias_kind_section()
    loop_two_site_section()
    loop_two_site_silent_section()
    loop_three_site_section()
    print("loop_two_site_gen:", list(loop_two_site_gen()))
    loop_two_site_read_before_section()
    loop_unpack_first_bind_section()
    print("loop_unpack_site_gen:", list(loop_unpack_site_gen()))
    loop_capture_section()
    loop_capture_quiet_section()
    unpack_kept_first_pass_section()
    view_through_call_section()


main()
