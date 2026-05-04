# D16 Phase 2: setattr(obj, "property_name", v) is rejected.
from tpy import Int32
from typing import Any

class Foo:
    _x: Int32
    _data: dict[str, Any]

    def __init__(self, x: Int32) -> None:
        self._x = x
        d: dict[str, Any] = {}
        self._data = d

    @property
    def x(self) -> Int32:
        return self._x

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

def main() -> None:
    f = Foo(Int32(1))
    setattr(f, "x", Int32(2))  # tpyc: error(/declared property/)

main()
