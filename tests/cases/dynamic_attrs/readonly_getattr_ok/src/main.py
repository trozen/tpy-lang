# D16: a @readonly method may call a @readonly __getattr__ -- happy path for
# the const-safe dyn-attr read.
from tpy import readonly
from typing import Any, cast

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d
        self._data["k"] = "v"

    @readonly
    def __getattr__(self, name: str) -> Any:
        return self._data[name]

    @readonly
    def peek(self) -> Any:
        return self.k

def main() -> None:
    b = Bag()
    print(cast(str, b.peek()))

main()
