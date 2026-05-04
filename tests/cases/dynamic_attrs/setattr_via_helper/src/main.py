# D16: dyn-attr writes work the same in a helper function as in __init__.
# Smoke test that the synthesized __setattr__ call resolves correctly when
# the receiver is a parameter, not `self`. Mutation-propagation enforcement
# itself is verified by error_readonly_calls_dyn_writer.
from typing import Any

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

    def __getattr__(self, name: str) -> Any:
        return self._data[name]

def populate(b: Bag) -> None:
    b.tag = "alpha"
    b.count = 1

def main() -> None:
    b = Bag()
    populate(b)
    print(len(b._data))

main()
