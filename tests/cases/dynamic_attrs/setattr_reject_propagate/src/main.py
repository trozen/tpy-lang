# `__setattr__` rejects via raise AttributeError; caller is @error_return(AttributeError),
# the rejection auto-propagates as the function's return error.
from tpy import error_return
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


@error_return(AttributeError)
def update_host(s: Strict) -> None:
    s.host = "ok"  # auto-propagate AttributeError out of update_host()


@error_return(AttributeError)
def update_private(s: Strict) -> None:
    s._private = "bad"  # auto-propagate


def main() -> None:
    s = Strict()
    try:
        update_host(s)
        print("ok host")
        update_private(s)
        print("never")
    except AttributeError as e:
        print("caught:", str(e))


main()
