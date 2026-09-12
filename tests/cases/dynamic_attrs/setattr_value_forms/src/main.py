# A dynamic-attr write whose value is NOT a literal: an int local, a value
# already typed Any, and a local at a __setattr__ whose value param is a
# concrete str rather than Any.
#
# The remaining combination -- a str LOCAL into an Any value slot -- is
# absent on purpose: it stores a view and panics on read-back
# (BUGS.md#any-str-local-stores-view), at every into-Any sink rather than at
# this one. Add the leg here when that entry is fixed.
from typing import Any, cast

from tpy import int32


class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

    def __getattr__(self, name: str) -> Any:
        return self._data[name]


class Strict:
    _n: str
    _v: str

    def __init__(self) -> None:
        self._n = ""
        self._v = ""

    def __setattr__(self, name: str, value: str) -> None:
        self._n = name
        self._v = value


def main() -> None:
    b = Bag()
    b.lit = 1  # the literal row, for contrast
    n = 7
    b.port = n  # a bare local: the into_any wrap over the name
    a: Any = 9
    b.raw = a  # already Any: no wrap at all
    print(cast(int, b.lit), cast(int32, b.port), cast(int, b.raw))

    s = Strict()
    v = "host"
    s.name = v  # a bare local at a CONCRETE (non-Any) value param
    print(s._n, s._v)


main()
