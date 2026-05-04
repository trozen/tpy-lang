# D16 Phase 3: del obj.declared_field is rejected (record layout is fixed).
from typing import Any

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __delattr__(self, name: str) -> None:
        del self._data[name]

def main() -> None:
    b = Bag()
    del b._data  # tpyc: error(/declared field/)

main()
