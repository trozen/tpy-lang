# Helper-call escape hatch: __setattr__ delegates rejection to a helper that
# raises AttributeError. The body-scan auto-apply only sees literal `raise`
# statements; user must decorate __setattr__ explicitly with
# @error_return(AttributeError) so the call is well-typed.
from tpy import error_return
from typing import Any


@error_return(AttributeError)
def reject_private(name: str) -> None:
    if name.startswith("_"):
        raise AttributeError(name)


class Strict:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    @error_return(AttributeError)
    def __setattr__(self, name: str, value: str) -> None:
        reject_private(name)
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
