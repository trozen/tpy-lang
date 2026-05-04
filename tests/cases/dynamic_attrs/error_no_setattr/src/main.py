# D16 Phase 2: undeclared attribute write on a class with __getattr__ but no __setattr__ is rejected.
from typing import Any

class ReadOnlyBag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __getattr__(self, name: str) -> Any:
        return self._data[name]

def main() -> None:
    b = ReadOnlyBag()
    b.x = "value"  # tpyc: error(/no field 'x' and does not define __setattr__/)

main()
