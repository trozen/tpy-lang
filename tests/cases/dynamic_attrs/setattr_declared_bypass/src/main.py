# D16 Divergence #1: declared-field writes bypass __setattr__ in TPy.
# Plain `self._counter = ...` inside __init__ is a direct declared-field
# write -- the dunder is NOT called. (CPython would route it through
# __setattr__ and recurse here, hence no_cpython.txt.)
from typing import Any, cast

class Counted:
    _counter: int
    _data: dict[str, Any]

    def __init__(self) -> None:
        self._counter = 0
        d: dict[str, Any] = {}
        self._data = d

    def __getattr__(self, name: str) -> Any:
        return self._data[name]

    def __setattr__(self, name: str, value: Any) -> None:
        # Fires only for undeclared names: _counter and _data writes don't
        # land here. The `+=` below also bypasses __setattr__ because
        # _counter is declared.
        self._counter += 1
        self._data[name] = value

def main() -> None:
    c = Counted()
    c.x = "hello"  # routes via __setattr__, increments counter
    c.y = "world"  # routes via __setattr__, increments counter
    # Internal field set in __init__ does NOT increment, so counter is 2.
    print(c._counter)
    print(cast(str, c.x))

main()
