# __setattr__ raises AttributeError to reject some writes; caller catches via
# try/except. Body-conditional auto-@error_return(AttributeError) makes the
# raise a return-tier signal -- no throw machinery on the call site.
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
    s.host = "ok"
    print("set host")
    try:
        s._private = "bad"
        print("never")
    except AttributeError as e:
        print("caught:", str(e))


main()
