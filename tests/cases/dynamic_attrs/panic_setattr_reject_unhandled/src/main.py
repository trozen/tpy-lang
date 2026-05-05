# `__setattr__` rejects at top-level (no try/except, no @error_return wrapper):
# panics at runtime since AttributeError has nowhere to propagate.
from typing import Any


class Strict:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: str) -> None:
        if name.startswith("_"):
            raise AttributeError(name)
        self._data[name] = value


def main() -> None:
    s = Strict()
    s._private = "bad"


main()
