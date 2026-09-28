# The return tier of try/except (a ReturnException handler) registers and retires
# its `as` capture on a separate code path from the throw tier, so shadowing must
# work there too. The tier accepts only one handler, hence no sibling arm here.
from typing import ClassVar

from tpy import int32, ReturnException, error_return


class Registry:
    code: ClassVar[int32] = 999


class NotFound(Exception, ReturnException):
    code: int32

    def __init__(self, code: int32) -> None:
        super().__init__()
        self.code = code


@error_return(NotFound)
def inner(key: str) -> int32:
    if key == "x":
        return 42
    raise NotFound(7)


def outer(key: str) -> int32:
    try:
        return inner(key)
    except NotFound as Registry:
        got = Registry.code
    return got


def main() -> None:
    print(outer("x"))
    print(outer("y"))
    print(Registry.code)


main()
