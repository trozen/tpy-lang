# D16: dyn-setattr on a readonly receiver must be rejected. Verifies the
# synth `__setattr__` call participates in readonly enforcement -- if it
# weren't recognized as mutating, this would compile.
from typing import Any
from tpy import readonly

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

def populate(b: readonly[Bag]) -> None:
    b.tag = "alpha"  # tpyc: error(/non-readonly|readonly|const/)

def main() -> None:
    pass

main()
