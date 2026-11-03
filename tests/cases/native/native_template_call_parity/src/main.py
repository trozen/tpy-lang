# The explicit `c.__getitem__(k)` call on a @native class compiles every shape
# its subscript twin `c[k]` does, through the record method-call row: a str
# key, a borrowed `Item | None` result, a `bytes` result -- and a named method
# with the same binding behaves the same. Template methods also take a str
# literal argument, a `Ptr` argument and return a tuple.
__tpy__ = {"includes": ["native_types.hpp"]}
from tpy import int32, readonly, Ptr
from tpy.extern import native, native_field, cpp_template


@native("nb::Item")
class Item:
    n: int32 = native_field("n_")


@native("nb::Table")
class Table:
    def __init__(self) -> None: ...

    @cpp_template("{self}.at({0})")
    def __getitem__(self, k: str) -> int32: ...

    @cpp_template("{self}.at({0})")
    def lookup(self, k: str) -> int32: ...


@native("nb::Bag")
class Bag:
    def __init__(self) -> None: ...

    @cpp_template("{self}.find({0})")
    def __getitem__(self, i: int32) -> Item | None: ...

    @cpp_template("{self}.find({0})")
    def find(self, i: int32) -> Item | None: ...

    @cpp_template("{self}.find(0)")
    def first_ptr(self) -> Ptr[Item]: ...

    @readonly
    @cpp_template("{self}.n_at({0})")
    def n_at(self, i: int32) -> int32: ...


@native("nb::Blobs")
class Blobs:
    def __init__(self) -> None: ...

    @native("nb::blob_at", function=True)
    def __getitem__(self, n: int32) -> bytes: ...

    @native("nb::blob_at", function=True)
    def prefix(self, n: int32) -> bytes: ...


@native("nb::Pairs")
class Pairs:
    def __init__(self) -> None: ...

    @cpp_template("nb::outs({self})")
    def outs(self) -> tuple[int32, int32]: ...

    @cpp_template("{self}.say({0})")
    def say(self, s: str) -> None: ...

    @cpp_template("nb::add_n({self}, {0})")
    def add_n(self, p: Ptr[Item]) -> None: ...

    @readonly
    @cpp_template("{self}.total")
    def total(self) -> int32: ...


def template_args(q: Pairs, c: Bag) -> None:
    # a tuple result unpacked, a str literal argument, a Ptr argument.
    a, b = q.outs()  # tpyc: ok
    q.say("hi")  # tpyc: ok
    p = c.first_ptr()
    q.add_n(p)  # tpyc: ok
    print("template_args", a, b, q.total())


def str_key(t: Table, k: str) -> None:
    # str key: the explicit call, its subscript twin, a named method.
    print("str_key", t.__getitem__(k), t[k], t.lookup(k))  # tpyc: ok


def opt_bind(c: Bag) -> None:
    # Optional result bound to a local; a write through it lands in the bag.
    p = c.__getitem__(0)  # tpyc: ok
    if p is not None:
        p.n = 7
    q = c[1]  # tpyc: ok
    if q is not None:
        q.n = 8
    print("opt_bind", c.n_at(0), c.n_at(1))


def opt_bind_named(c: Bag) -> None:
    # the named twin: a write through its result lands in the bag too.
    s = c.find(0)  # tpyc: ok
    if s is not None:
        s.n = 70
    print("opt_bind_named", c.n_at(0))


def opt_bind_named_none(c: Bag) -> None:
    r = c.find(5)  # tpyc: ok
    print("opt_bind_named", r is None)


def opt_ret(c: Bag, i: int32) -> Item | None:
    # Optional result returned (the subscript twin `return c[i]` is the
    # sidecar unit's, not yet admitted).
    return c.__getitem__(i)  # tpyc: ok


def opt_ret_named(c: Bag, i: int32) -> Item | None:
    return c.find(i)  # tpyc: ok


def bytes_ret(b: Blobs) -> None:
    # bytes result through a function-form native binding.
    x = b.__getitem__(2)  # tpyc: ok
    y = b[1]  # tpyc: ok
    z = b.prefix(3)  # tpyc: ok
    print("bytes_ret", x, y, z)


def bump(c: Bag) -> None:
    p = opt_ret(c, 1)
    if p is not None:
        p.n = 9


def main() -> None:
    str_key(Table(), "b")
    c = Bag()
    opt_bind(c)
    opt_bind_named(c)
    opt_bind_named_none(c)
    bump(c)
    print("opt_ret", c.n_at(1), opt_ret(c, 4) is None,
          opt_ret_named(c, 0) is not None)
    bytes_ret(Blobs())
    template_args(Pairs(), c)


main()
