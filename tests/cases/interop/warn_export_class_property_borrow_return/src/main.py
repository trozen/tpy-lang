# A property getter returning a borrow with no aliasing path -- a list
# (copied IN, no PyObject behind it) or a REASSIGNABLE class field -- copies
# out at the boundary, so `obj.items.append(...)` or `obj.alt.x = 5` from
# Python silently mutates a throwaway copy: warn at the return, like the
# method borrow-return warning. A never-reassigned class field crosses as an
# aliasing borrow view (no warning), and Own[...] of a fresh or copy()'d
# value stays quiet.
# tpy: ext_module
from tpy import Int64, Own
from tpy.extern import export


@export
class Inner:
    x: Int64

    def __init__(self) -> None:
        self.x = 0


@export
class Box:
    _items: list[Int64]
    _inner: Inner
    _alt: Inner

    def __init__(self) -> None:
        self._items = [1, 2]
        self._inner = Inner()
        self._alt = Inner()

    def rebind_alt(self) -> None:
        self._alt = Inner()

    @property
    def items(self) -> list[Int64]:
        return self._items  # tpyc: warning(/property 'items': returns a list by reference.*copied across the CPython boundary.*return Own/)

    @property
    def inner(self) -> Inner:
        # `_inner` is never reassigned outside __init__: crosses as an
        # aliasing borrow view, so nothing copies and nothing warns.
        return self._inner  # tpyc: ok

    @property
    def alt(self) -> Inner:
        return self._alt  # tpyc: warning(/property 'alt': returns exposed class 'Inner' by reference.*identity and write-through aliasing are not preserved/)

    @property
    def snapshot(self) -> Own[list[Int64]]:  # tpyc: ok
        out: list[Int64] = []
        for x in self._items:
            out.append(x)
        return out
