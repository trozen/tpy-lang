# A `str` element read off a NAME container, held across a call that hands the
# container to an imported callee or its local twin. Under the one view rule an
# element (and a record field) never lends a view, so every leg binds an owned
# copy whatever the callee's signature, and its C++ form cannot depend on where
# the callee lives. The legs pin that the copy keeps CPython's value across the
# write, the `nowrite` and `readonly` callees and a second NAME bound to the
# container. The `readonly` legs and the field section own like the others (an
# element or a field read could stay a view; they own too and contrast nothing.
from typing import Iterator

from tpy import Own, int32, readonly
from viewmod import Outer, bump, touch, peek, peek_rec


def bump_local(xs: list[str]) -> None:
    xs[0] = "local"


def touch_local(xs: list[str]) -> int32:
    return len(xs)


def peek_local(xs: readonly[list[str]]) -> int32:
    return len(xs)


# free function: the imported callee writes the very element that was read
def sec_imported_write(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    bump(xs)
    print("imported write", v, xs[0])


# the imported callee never writes
def sec_imported_nowrite(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    print("imported nowrite", v, touch(xs))


# a `readonly` parameter closes the callee's OWN write path, imported like
# local; the read owns a copy all the same
def sec_imported_readonly(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    print("imported readonly", v, peek(xs))


def sec_local_write(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    bump_local(xs)
    print("local write", v, xs[0])


def sec_local_nowrite(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    print("local nowrite", v, touch_local(xs))


def sec_local_readonly(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    print("local readonly", v, peek_local(xs))


# method: the call site sits in a record body
class Runner:
    def run(self, xs: list[str]) -> None:
        v = xs[0]  # tpyc: type(str)
        bump(xs)
        print("method", v, xs[0])


# generator: the copy lives in the frame across the suspension
def sec_gen(xs: list[str]) -> Iterator[int32]:
    v = xs[0]  # tpyc: type(str)
    yield len(v)
    bump(xs)
    print("gen", v, xs[0])


# no callee at all: a second NAME bound to the container
def sec_alias_bind(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    ys = xs
    print("alias bind", v, len(ys))


# a one-hop field read beside a `readonly` callee: an owned copy, like the
# element reads above
def sec_field(o: Outer) -> None:
    v = o.name  # tpyc: type(str)
    print("field", v, peek_rec(o))


def main() -> None:
    # each callee takes a mutable container reference, so the argument is a
    # named local rather than a literal
    a = ["i1", "x"]
    sec_imported_write(a)
    b = ["i2", "x"]
    sec_imported_nowrite(b)
    c = ["i3", "x"]
    sec_imported_readonly(c)
    d = ["l1", "x"]
    sec_local_write(d)
    e = ["l2", "x"]
    sec_local_nowrite(e)
    f = ["l3", "x"]
    sec_local_readonly(f)
    g = ["m1", "x"]
    Runner().run(g)
    h = ["g1", "x"]
    for n in sec_gen(h):
        print("gen yield", n)
    i = ["a1", "x"]
    sec_alias_bind(i)
    sec_field(Outer("f1"))


main()
