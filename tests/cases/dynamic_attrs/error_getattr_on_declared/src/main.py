# D16 Phase 1: getattr(obj, "declared_field") is rejected; use direct attribute access.
from tpy import Int32
from typing import Any

class Bag:
    _data: dict[str, Any]

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        return self._data[name]

def main() -> None:
    b = Bag({})
    x = getattr(b, "_data")  # tpyc: error(/declared field/)
    print(x)

main()
