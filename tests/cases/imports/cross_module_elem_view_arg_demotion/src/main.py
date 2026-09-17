# A `str` element read off a NAME container is a VIEW of that element's buffer,
# and handing the container to a callee through a non-`readonly` parameter is a
# write path to it -- so the read falls back to an owned copy. The gate is the
# callee's declared SIGNATURE, and it must read the same whether the callee is
# imported or local: mutation facts are per module, so an imported verdict is
# already frozen while an intra-module one is not, and letting that decide would
# make the local's C++ form depend on where its callee lives. Each leg pairs an
# imported callee with its local twin; the `nowrite` legs declare a mutable
# parameter but never write, and demote anyway. `sec_alias_bind` is the leg
# with no callee: a second NAME on the container demotes on the bind alone.
# The last section is the contrast: a one-hop record FIELD read is a view of
# the field's buffer and a `readonly` callee is no write path to it, so it
# keeps the view the mutable-parameter legs above lose.
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


# the imported callee never writes; the mutable parameter alone demotes
def sec_imported_nowrite(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    print("imported nowrite", v, touch(xs))


# a `readonly` parameter closes the callee's OWN write path, imported like
# local -- not a guarantee that nothing writes: the same callee may append to a
# module global aliasing `xs` and reallocate the buffer under `v` with no
# diagnostic (BUGS.md#readonly-method-global-write-under-live-borrow)
def sec_imported_readonly(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(StrView)
    print("imported readonly", v, peek(xs))


def sec_local_write(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    bump_local(xs)
    print("local write", v, xs[0])


def sec_local_nowrite(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    print("local nowrite", v, touch_local(xs))


def sec_local_readonly(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(StrView)
    print("local readonly", v, peek_local(xs))


# method: the call site sits in a record body
class Runner:
    def run(self, xs: list[str]) -> None:
        v = xs[0]  # tpyc: type(str)
        bump(xs)
        print("method", v, xs[0])


# generator: the view lives in the frame across the suspension
def sec_gen(xs: list[str]) -> Iterator[int32]:
    v = xs[0]  # tpyc: type(str)
    yield len(v)
    bump(xs)
    print("gen", v, xs[0])


# no callee at all: binding a second NAME to the container is itself a write
# path to every element, so the view demotes on the bind alone
def sec_alias_bind(xs: list[str]) -> None:
    v = xs[0]  # tpyc: type(str)
    ys = xs
    print("alias bind", v, len(ys))


# the contrast: a one-hop field read keeps its view beside a `readonly`
# callee, the same answer the element read gets at a `readonly` parameter
def sec_field(o: Outer) -> None:
    v = o.name  # tpyc: type(StrView)
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
