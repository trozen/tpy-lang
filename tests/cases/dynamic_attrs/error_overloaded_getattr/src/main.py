# D16 Phase 4: __getattr__ cannot be @overload.
from typing import Any, overload

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    @overload
    def __getattr__(self, name: str) -> str: ...  # tpyc: error(/cannot be @overload/)
    @overload
    def __getattr__(self, name: str) -> int: ...
    def __getattr__(self, name: str) -> Any:
        return self._data[name]
