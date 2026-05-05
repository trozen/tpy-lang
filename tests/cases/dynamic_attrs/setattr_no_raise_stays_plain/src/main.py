# Fast path: __setattr__ that never raises stays plain (no @error_return).
# Generated dunder returns void, call sites are bare assignments.
from typing import Any


class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: str) -> None:
        self._data[name] = value


def main() -> None:
    b = Bag()
    b.host = "example.com"
    b.other = "x"
    print("done")


main()
