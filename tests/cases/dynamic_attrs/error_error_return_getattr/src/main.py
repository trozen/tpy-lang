# D16: __getattr__ cannot be @error_return -- attribute-access routing should
# not surface expected<T, E> machinery into every undeclared `obj.foo` site.
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
    def __getattr__(self, name: str) -> Any:  # tpyc: error(/cannot use @error_return/)
        return self._data[name]

def main() -> None:
    pass

main()
