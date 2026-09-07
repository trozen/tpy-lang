# `setattr` whose VALUE is a call: the dynamic-attribute write mirror admits
# only sources whose render is placeholder-transparent.
from typing import Any


class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value


def make() -> str:
    return "x"


def use(b: Bag) -> None:
    # The value is a call rvalue, not a name or a literal.
    setattr(b, "who", make())  # tpyc: error(/stmt\.assign/)


def main() -> None:
    use(Bag())


main()
