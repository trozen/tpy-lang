# D16 Phase 3: del obj.foo on undeclared name routes through __delattr__.
from typing import Any, cast

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

    def __delattr__(self, name: str) -> None:
        del self._data[name]

    def __getattr__(self, name: str) -> Any:
        return self._data[name]

def main() -> None:
    b = Bag()
    b.x = "hello"
    print(cast(str, b.x))
    del b.x
    print(len(b._data))

main()
