# D16: a @readonly method cannot call a non-readonly __getattr__.
from tpy import readonly
from typing import Any

class Bag:
    _data: dict[str, Any]
    _counter: int

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d
        self._counter = 0

    def __getattr__(self, name: str) -> Any:
        # Mutates self -- not @readonly.
        self._counter += 1
        return name

    @readonly
    def peek(self) -> Any:
        return self.foo  # tpyc: error(/non-readonly|readonly|const/)

def main() -> None:
    b = Bag()
    print(b.peek())

main()
