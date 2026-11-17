import ea
from typing import Protocol


# A protocol base is read when the class is defined.
class C(ea.Proto):  # tpyc: warning(/'ea.Proto' is read while 'ea' is still being imported.*AttributeError \(partially initialized module 'ea' has no attribute 'Proto'\)/)
    def f(self) -> int:
        return 2


# A parameter default is evaluated when the def runs.
def pick(c: "ea.Color" = ea.Color.RED) -> int:  # tpyc: warning(/'ea.Color' is read while 'ea'.*AttributeError/)
    return c.value


class Holder:
    # A class-body field default is evaluated with the class body.
    c: "ea.Color" = ea.Color.BLUE  # tpyc: warning(/'ea.Color' is read while 'ea'.*AttributeError/)

    # A method's default is evaluated when the class body runs the def.
    def m(self, c: "ea.Color" = ea.Color.RED) -> int:  # tpyc: warning(/'ea.Color' is read while 'ea'.*AttributeError/)
        return c.value


# A macro decorator re-exported by `ea` is read off it at the class.
# `ea.field(...)` is skipped: BUGS.md#macro-field-call-through-module-reexport-rejects.
@ea.dataclass  # tpyc: warning(/'ea.dataclass' is read while 'ea'.*AttributeError/)
class D:
    x: int = 3


# CPython 3.12 and 3.13 evaluate an unquoted annotation at the def.
def bump(it: ea.Item) -> None:  # tpyc: warning(/CPython 3.12 and 3.13 cannot import this: the annotation reads 'ea.Item'.*Quote the whole annotation/)
    it.n += 1


# A protocol's member annotations are evaluated with its class body.
class Sink(Protocol):
    last: ea.Item  # tpyc: warning(/the annotation reads 'ea.Item'/)

    def put(self, i: ea.Item) -> None: ...  # tpyc: warning(/the annotation reads 'ea.Item'/)


# A peer read nested in an annotation is evaluated with it.
def first(xs: list[ea.Item]) -> "ea.Item":  # tpyc: warning(/the annotation reads 'ea.Item'/)
    return xs[0]


def maybe(xs: "list[ea.Item]") -> ea.Item | None:  # tpyc: warning(/the annotation reads 'ea.Item'/)
    return xs[0] if xs else None


# A decorated class is named by its `class` line, not its decorator's.
def shift(p: ea.Pt) -> None:  # tpyc: warning(/the annotation reads 'ea.Pt' .* before it defines 'Pt' \(ea.py:24\)/)
    p.x += 1


def main() -> None:
    h = Holder()
    xs = [ea.Item(1)]
    # `first` and `maybe` return the element itself, not a copy.
    first(xs).n += 1
    got = maybe(xs)
    if got is not None:
        got.n += 1
    print("eager:", C().f(), pick(), h.c.value, h.m(), D().x, xs[0].n)
