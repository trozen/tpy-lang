# A str/bytes tuple-unpack target off a tuple NAME views that tuple's element,
# like `a = t[0]`: a reseat of the tuple while the target is live (a rebind,
# a nested def's nonlocal rebind, a `del`) makes the target own its buffer
# (std::string), and without a reseat it stays a view.
from typing import Iterator

from tpy import StrView


def mk(i: int) -> tuple[str, int]:
    return ("payload-number-" + str(i) + "-long-enough-to-defeat-sso", i)


def mkb(i: int) -> tuple[bytes, int]:
    return (b"bytes-payload-long-enough-to-defeat-small-buffers-" + str(i).encode(), i)


def find(i: int) -> tuple[str, int] | None:
    if i < 0:
        return None
    return ("found-payload-" + str(i) + "-long-enough-to-defeat-sso", i)


# free function: the source tuple is rebound while `a` is live.
def plain() -> None:
    t = mk(1)
    a, b = t  # tpyc: ok
    t = mk(2)
    print("plain", a, b, t[1])


# narrowed Optional[tuple] source rebound to another tuple, then to None.
def narrowed() -> None:
    r = find(1)
    if r is not None:
        a, b = r  # tpyc: ok
        r = find(2)
        print("narrowed", a, b)
    r2 = find(3)
    if r2 is not None:
        c, d = r2  # tpyc: ok
        r2 = None
        print("narrowed_none", c, d)


# bytes element.
def bytes_elem() -> None:
    t = mkb(1)
    a, b = t  # tpyc: ok
    t = mkb(2)
    print("bytes_elem", a, b)


# the reseat comes later in the loop body, read in the next iteration.
def loop_reseat() -> None:
    t = mk(0)
    for i in range(3):
        a, b = t  # tpyc: ok
        t = mk(i + 10)
        print("loop_reseat", a, b)


# closure: a nested def rebinds the source via nonlocal; the int element's
# `const&` target must not follow the rebind either.
def closure_reseat() -> None:
    t = mk(1)
    a, b = t  # tpyc: ok

    def reseat() -> None:
        nonlocal t
        t = mk(2)

    reseat()
    print("closure_reseat", a, b)


# the subscript spelling with the nested def declared before the view.
def closure_subscript() -> None:
    t = mk(1)

    def reseat() -> None:
        nonlocal t
        t = mk(2)

    a = t[0]  # tpyc: ok
    reseat()
    print("closure_subscript", a)


# `del` of the source ends its storage: the unpack target, the subscript
# view and the field view own, and the int element is copied, not `const&`.
def del_source() -> None:
    t = mk(1)
    a, b = t  # tpyc: ok
    del t
    print("del_unpack", a, b)
    u = mk(2)
    s = u[0]  # tpyc: ok
    del u
    print("del_subscript", s)
    h = Named("named-holder-long-enough-to-defeat-sso")
    f = h.name  # tpyc: ok
    del h
    print("del_field", f)


# a nested def deletes the source through nonlocal.
def nested_del() -> None:
    t = mk(1)
    a, b = t  # tpyc: ok

    def drop() -> None:
        nonlocal t
        del t

    drop()
    print("nested_del", a, b)


# a SIBLING closure rebinds the source while this closure's view is live.
def sibling_closure() -> None:
    t = mk(1)

    def reseat() -> None:
        nonlocal t
        t = mk(2)

    def read() -> None:
        a, b = t  # tpyc: ok
        reseat()
        print("sibling_closure", a, b)

    read()


# inverse: a nested def that only READS the source keeps the view.
def closure_reads() -> None:
    t = mk(1)

    def peek() -> int:
        return t[1]

    a, b = t  # tpyc: ok
    print("closure_reads", a, b, peek())


class Named:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def nv(self) -> StrView:
        return self.name


# `del` of the receiver of a method that hands back a view of its field.
def del_method_view(flag: bool) -> None:
    h = Named("method-view-holder-long-enough-to-defeat-sso")
    v = h.nv()  # tpyc: ok
    del h
    print("del_method_view", v)
    g = Named("hoisted-method-view-holder-long-enough-sso")
    if flag:
        w = g.nv()  # tpyc: ok
    else:
        w = "else"
    del g
    print("del_hoisted_method_view", w)


class Holder:
    n: int

    def __init__(self) -> None:
        self.n = 0

    # method body.
    def reseat(self) -> None:
        t = mk(1)
        a, b = t  # tpyc: ok
        t = mk(2)
        print("method", a, b, t[1])


# inverse: no reseat, the target stays a zero-copy view.
def no_reseat() -> None:
    t = mk(1)
    a, b = t  # tpyc: ok
    print("no_reseat", a, b)


# inverse: the subscript twin keeps its own verdict (owned after a reseat).
def subscript_twin() -> None:
    t = mk(1)
    a = t[0]  # tpyc: ok
    t = mk(2)
    print("subscript_twin", a, t[1])


# inverse: a generator frame already owns the target.
def gen() -> Iterator[str]:
    t = mk(1)
    a, b = t  # tpyc: ok
    t = mk(2)
    yield a


def main() -> None:
    plain()
    narrowed()
    bytes_elem()
    loop_reseat()
    closure_reseat()
    closure_subscript()
    del_source()
    del_method_view(True)
    nested_del()
    sibling_closure()
    closure_reads()
    Holder().reseat()
    no_reseat()
    subscript_twin()
    for v in gen():
        print("gen", v)


main()
