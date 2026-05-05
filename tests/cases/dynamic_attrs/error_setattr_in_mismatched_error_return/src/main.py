# Caller has @error_return(SomeOtherType): AttributeError from `obj.foo = v` cannot
# auto-propagate as a different return type. Sema rejects with try/except hint.
from tpy import error_return, ReturnException
from typing import Any


class StopIt(Exception, ReturnException):
    pass


class Strict:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: str) -> None:
        if name.startswith("_"):
            raise AttributeError(name)
        self._data[name] = value


@error_return(StopIt)
def update(s: Strict) -> None:
    s.host = "ok"  # tpyc: error(/may return 'AttributeError' which must be handled/)


def main() -> None:
    pass


main()
