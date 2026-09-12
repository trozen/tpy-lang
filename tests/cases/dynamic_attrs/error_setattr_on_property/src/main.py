# D16 Phase 2: setattr(obj, "property_name", v) is rejected.
from tpy import int32
from typing import Any

class Foo:
    _x: int32
    _data: dict[str, Any]

    def __init__(self, x: int32) -> None:
        self._x = x
        d: dict[str, Any] = {}
        self._data = d

    @property
    def x(self) -> int32:
        return self._x

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

def main() -> None:
    f = Foo(int32(1))
    setattr(f, "x", int32(2))  # tpyc: error(/declared property/)

main()
