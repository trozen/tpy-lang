# A readonly-borrow return and a @nocopy argument through a callable.
from typing import Callable
from tpy import Fn, readonly
from fixtures import P, Pin


# a readonly borrow bound to a local and returned: it lends the argument,
# so a write to the source after the call shows through the result.
def ro_ident(p: P) -> readonly[P]:
    return p


def apply_ro(f: Callable[[P], readonly[P]], x: P) -> readonly[P]:
    r = f(x)
    return r  # tpyc: ok


def sec_readonly() -> None:
    # A lambda at a readonly-borrow slot lends its argument const.
    k: Callable[[P], readonly[P]] = lambda q: q  # tpyc: ok
    a = P(1)
    ka = k(a)
    a.n += 2
    print("readonly lambda:", a.n, ka.n)
    p = P(1)
    # A named Callable, not a converted temporary: GCC's -Wdangling-reference
    # takes a temporary argument for the referent (as in
    # BUGS.md#gcc-dangling-reference-borrow-tuple-arg).
    g: Callable[[P], readonly[P]] = ro_ident
    r = apply_ro(g, p)
    p.n += 5
    print("readonly:", p.n, r.n)


# @nocopy: the borrow is the only way the result can come back.
def touch(f: Fn[[Pin], Pin], x: Pin) -> None:
    r = f(x)  # tpyc: ok
    r.n += 1


def keep_pin(p: Pin) -> Pin:
    return p


def sec_nocopy() -> None:
    p = Pin(1)
    touch(keep_pin, p)
    touch(lambda q: q, p)
    print("nocopy:", p.n)


def main() -> None:
    sec_readonly()
    sec_nocopy()
