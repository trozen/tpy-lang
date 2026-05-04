# D16 Phase 5: child class inherits __setattr__ via MRO; undeclared writes route correctly.
from typing import Any, cast

class Base:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

    def __getattr__(self, name: str) -> Any:
        return self._data[name]

class Child(Base):
    def __init__(self) -> None:
        super().__init__()

def main() -> None:
    c = Child()
    c.color = "blue"
    print(cast(str, c.color))

main()
