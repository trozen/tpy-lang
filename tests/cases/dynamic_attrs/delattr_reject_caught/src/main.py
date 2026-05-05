# __delattr__ raises AttributeError to reject some deletes; caller catches.
# Mirror of setattr_reject_caught.
from typing import Any


class Strict:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {"a": 1, "b": 2}
        self._data = d

    def __delattr__(self, name: str) -> None:
        if name.startswith("_"):
            raise AttributeError(name)
        del self._data[name]


def main() -> None:
    s = Strict()
    del s.a
    print("deleted a")
    try:
        del s._private
        print("never")
    except AttributeError as e:
        print("caught:", str(e))


main()
