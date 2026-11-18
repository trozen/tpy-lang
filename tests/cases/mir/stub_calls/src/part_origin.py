# A stub's result is fresh unless its declaration names where it comes from (`borrows=`):
# a fresh temporary kept by a view dangles, a declared origin aliases, an undeclared one does not.
import os
from typing import Iterator
from tpy import StrView, pure, readonly
from tpy.extern import cpp_template, native


class H:
    v: StrView

    def __init__(self) -> None:
        self.v = ""


def set_v(h: H, s: StrView) -> None:
    h.v = s


class Box:
    def __init__(self, n: int) -> None:
        self.n = n


def set_and_read(r: Box, k: int) -> int:
    r.n = 99
    return k


# repr_dangle: repr's fresh result dies with its statement while h.v still views it (never run)
def repr_dangle(h: H, p: str) -> None:
    set_v(h, repr(p))  # tpyc: warning(/the temporary 'repr\(p\)' lives only until the end of this statement, and 'h.v' may still view it and is read on line /)
    print("repr_dangle", h.v)


# repr_bound: the safe twin -- the fresh result is a local that outlives the read
def repr_bound(p: str) -> None:  # tpyc: mir(covered)
    h = H()
    text = repr(p)
    set_v(h, text)  # tpyc: ok mir_borrows(h.v, static|text)
    print("repr_bound", h.v)


class Keeper:
    h: H

    def __init__(self) -> None:
        self.h = H()

    # repr_method: the same dangle from a method body (never run)
    def keep(self, p: str) -> None:
        set_v(self.h, repr(p))  # tpyc: warning(/the temporary 'repr\(p\)' lives only until the end of this statement/)
        print("repr_method", self.h.v)


# min_alias: `min` over int declares `borrows=`, so its result is `r.n` while `set_and_read` writes `r`
# (the run writes the value `r.n` already holds, so its output matches CPython;
# the call-site copy that would make it exact: BUGS.md#borrowed-arg-into-mutated-storage)
def min_alias(r: Box, b: int) -> int:
    return set_and_read(r, min(r.n, b))  # tpyc: warning(/the argument for parameter 'k' is what 'set_and_read' modifies through parameter 'r' while it still uses 'k'/)


# round_alias: `round(int)` renders `(r.n)` and declares it (BUGS.md#borrowed-arg-into-mutated-storage)
def round_alias(r: Box) -> int:
    return set_and_read(r, round(r.n))  # tpyc: warning(/the argument for parameter 'k' is what 'set_and_read' modifies through parameter 'r' while it still uses 'k'/)


# abs_fresh: `abs` over int declares no origin and `BigInt::abs` returns by value: no alias
def abs_fresh(r: Box) -> int:  # tpyc: mir(covered)
    return set_and_read(r, abs(r.n))  # tpyc: ok


# abs_generator: the same fresh result from a generator body
def abs_generator(r: Box) -> Iterator[int]:
    yield set_and_read(r, abs(r.n))  # tpyc: ok


# abs_closure: the same fresh result from a nested def; MIR does not lower this body,
# so the line pins only that the call compiles without a warning
def abs_closure(r: Box) -> int:  # tpyc: mir(uncovered /unsupported statement/)
    def inner() -> int:
        return set_and_read(r, abs(r.n))  # tpyc: ok
    return inner()


# same_alias: an undeclared template rendering its argument, `({0})`, is that argument (never run)
@readonly
@cpp_template("({0})", transient=True)
def same(x: int) -> int: ...


def same_alias(r: Box) -> int:
    return set_and_read(r, same(r.n))  # tpyc: warning(/the argument for parameter 'k' is what 'set_and_read' modifies through parameter 'r' while it still uses 'k'/)


# same_s_alias: the `str` twin -- the template substitutes the argument expression, so `(r.s)`
# is a reference to `r.s`, not a copy of the view (never run)
@readonly
@cpp_template("({0})", transient=True)
def same_s(x: str) -> str: ...


class SBox:
    def __init__(self, s: str) -> None:
        self.s = s


def set_and_len(r: SBox, k: str) -> int:
    r.s = "changed"
    return len(k)


def same_s_alias(r: SBox) -> int:
    return set_and_len(r, same_s(r.s))  # tpyc: warning(/the argument for parameter 'k' is what 'set_and_len' modifies through parameter 'r' while it still uses 'k'/)


# same_s_view: `same_s(a)` renders `(a)`, so `h.v` may view `a` (or keep what it viewed), never a dying temporary (never run)
def same_s_view(h: H, a: str) -> None:
    set_v(h, same_s(a))  # tpyc: ok mir_borrows(h.v, a|h.v)
    print("same_s_view", h.v)


# native_min_alias: a free `@native` binding's declaration is believed as written -- `borrows=` over
# `str` views names the arguments, so the result is `r.s` while `set_and_len` reassigns it (never run)
@pure
@readonly
@native("std::min", borrows=("a", "b"))
def smin(a: str, b: str) -> str: ...


class TBox:
    def __init__(self, s: str, t: str) -> None:
        self.s = s
        self.t = t


def set_and_len2(r: TBox, k: str) -> int:
    r.s = "changed"
    return len(k)


def native_min_alias(r: TBox) -> int:
    return set_and_len2(r, smin(r.s, r.t))  # tpyc: warning(/the argument for parameter 'k' is what 'set_and_len2' modifies through parameter 'r' while it still uses 'k'/)


# get_dangle: `dict.get` at a `str` value returns a new string whatever it declares, so the view dangles (never run)
def get_dangle(h: H, d: dict[str, str], k: str, fb: str) -> None:
    set_v(h, d.get(k, fb))  # tpyc: warning(/the temporary 'd.get\(k, fb\)' lives only until the end of this statement, and 'h.v' may still view it/)
    print("get_dangle", h.v)


# pick_dangle: a template building a fresh string declares no origin, so the view dangles (never run;
# declaring `borrows=` here would be taken at its word: BUGS.md#undeclared-binding-reference-result-taken-fresh)
@pure
@cpp_template("(std::string({0}) + std::string({1}))")
def pick(a: str, b: str) -> str: ...


def pick_dangle(h: H, a: str, b: str) -> None:
    set_v(h, pick(a, b))  # tpyc: warning(/the temporary 'pick\(a, b\)' lives only until the end of this statement, and 'h.v' may still view it/)
    print("pick_dangle", h.v)


# os_readlink: transient=True, a view in and a fresh string out
def os_readlink(name: str) -> str:  # tpyc: mir(covered)
    return os.readlink(name)  # tpyc: ok


# os_realpath: transient=True, a view in and a fresh string out
def os_realpath(name: str) -> str:  # tpyc: mir(covered)
    return os.path.realpath(name)  # tpyc: ok


def main() -> None:
    repr_bound("q")
    r = Box(99)
    print("min_alias", min_alias(r, 100), r.n)
    print("round_alias", round_alias(Box(99)))
    print("abs_fresh", abs_fresh(Box(-3)))
    for v in abs_generator(Box(-4)):
        print("abs_generator", v)
    print("abs_closure", abs_closure(Box(-5)))
    # The target exists, so realpath resolves the link; its base name is host-independent.
    os.mkdir("origin_target")
    os.symlink("origin_target", "origin_link")
    print("os_readlink", os_readlink("origin_link"))
    print("os_realpath", os.path.basename(os_realpath("origin_link")))
    os.unlink("origin_link")
    os.rmdir("origin_target")
