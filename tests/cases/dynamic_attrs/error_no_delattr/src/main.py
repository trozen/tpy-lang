# D16 Phase 3: del obj.undeclared on a class without __delattr__ is rejected.
from typing import Any

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __getattr__(self, name: str) -> Any:
        return self._data[name]

def main() -> None:
    b = Bag()
    del b.x  # tpyc: error(/no field 'x' and does not define __delattr__/)

main()
