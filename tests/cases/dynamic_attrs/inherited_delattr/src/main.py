# D16 Phase 5: child class inherits __delattr__ via MRO; del routes correctly.
from typing import Any

class Base:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

    def __delattr__(self, name: str) -> None:
        del self._data[name]

class Child(Base):
    def __init__(self) -> None:
        super().__init__()

def main() -> None:
    c = Child()
    c.x = "value"
    del c.x
    print(len(c._data))

main()
