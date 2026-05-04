# D16 Phase 4: a @readonly method cannot call __setattr__ (writes are mutating).
from tpy import readonly
from typing import Any

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

    @readonly
    def touch(self) -> None:
        self.x = "hi"  # tpyc: error(/readonly|mutat|const/)

def main() -> None:
    b = Bag()
    b.touch()

main()
