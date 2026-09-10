# __getattr__ cannot be a @dispatch set, and the rejection names @dispatch.
from typing import Any
from tpy import dispatch


class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    @dispatch
    def __getattr__(self, name: str) -> str:  # tpyc: error(/cannot be @dispatch/)
        return "s"

    @dispatch
    def __getattr__(self, name: Any) -> int:
        return 0
