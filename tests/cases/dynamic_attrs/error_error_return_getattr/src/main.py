# D16: __getattr__ implicitly uses @error_return(AttributeError); user may not
# override with a different ReturnException type.
from tpy import error_return, ReturnException
from typing import Any

class AttrErr(Exception, ReturnException):
    pass

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    @error_return(AttrErr)
    def __getattr__(self, name: str) -> Any:  # tpyc: error(/may only use @error_return\(AttributeError\)/)
        return self._data[name]

def main() -> None:
    pass

main()
