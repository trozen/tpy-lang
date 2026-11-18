# An escaping nested def capturing a generic `T` parameter holds its storage
# form: its own value at a value type, the caller's object at a reference type.
from typing import Callable
from tpy import ValueType, int32, readonly

from fixtures import C, HasGet, V, make_str
import part_method
import part_shapes
import part_slots


def mk[T](x: T) -> Callable[[str], None]:
    def g(tag: str) -> None:  # tpyc: ok
        print(tag, x)  # the capture under test: unbounded T
    return g


def mkv[T: ValueType](x: T) -> Callable[[str], None]:
    def g(tag: str) -> None:  # tpyc: ok
        print(tag, x)  # ValueType-bounded T
    return g


def mkp[T: HasGet](x: T) -> Callable[[str], None]:
    def g(tag: str) -> None:  # tpyc: ok
        print(tag, x.get())  # protocol-bounded T
    return g


def mkr[T](x: readonly[T]) -> Callable[[str], None]:
    def g(tag: str) -> None:  # tpyc: ok
        print(tag, x)  # readonly[T]
    return g


def nonesc[T](x: T, c: C) -> None:
    def g() -> None:
        print("nonesc", x)  # non-escaping: still reads the caller's object
    c.n = 77
    g()


def scope_v() -> Callable[[str], None]:
    # The caller's local dies before the closure runs.
    local = V(make_str(10))
    return mk(local)


def main() -> None:
    # unbounded T at each instantiation kind
    mk(5)("u.int32")
    mk(V("hello" * 5))("u.V")
    f_str = mk(make_str(10))  # temporary argument
    f_str("u.str")
    big: int = 10 ** 30
    mk(big * 2)("u.BigInt")
    mk((1, "xy" * 10))("u.tuple")
    c = C(1)
    f_c = mk(c)
    c.n = 42  # mutated after the closure exists: seen
    f_c("u.C")
    scope_v()("s.V")
    # ValueType-bounded T
    mkv(7)("v.int32")
    f_vstr = mkv(make_str(10))  # temporary argument, gone before the call
    junk = make_str(11)
    f_vstr("v.str")
    mkv(V("world" * 5))("v.V")
    # protocol-bounded T
    mkp(V("p" * 30))("p.V")
    cp = C(3)
    f_p = mkp(cp)
    cp.n = 33
    f_p("p.C")
    # readonly[T]
    cr = C(4)
    f_r = mkr(cr)
    cr.n = 44
    f_r("r.C")
    mkr(make_str(10))("r.str")
    # non-escaping nested def
    cn = C(0)
    nonesc(cn, cn)
    print("junk", len(junk))
    part_method.main()
    part_shapes.main()
    part_slots.main()


main()
