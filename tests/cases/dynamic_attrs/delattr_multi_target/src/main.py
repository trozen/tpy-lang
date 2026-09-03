# `del a.x, b.y` -- a MULTI-target attribute delete through __delattr__,
# emitted as one call line per target in source order.
from typing import Any, cast


class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

    def __delattr__(self, name: str) -> None:
        del self._data[name]

    def __getattr__(self, name: str) -> Any:
        return self._data[name]


def main() -> None:
    a = Bag()
    a.x = 1
    a.keep = 2
    b = Bag()
    b.y = 3
    del a.x, b.y  # both targets are deleted, left to right
    print(len(a._data), len(b._data), cast(int, a.keep))

    c = Bag()
    c.p = 1
    c.q = 2
    del c.p, c.q  # two targets on the SAME receiver
    print(len(c._data))


main()
