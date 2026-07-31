# The return tier of try/except (a ReturnException handler) registers and retires
# its `as` capture on a separate code path from the throw tier, so shadowing must
# work there too. The tier accepts only one handler, hence no sibling arm here.
from typing import ClassVar

from tpy import Int32, ReturnException, error_return


class Registry:
    code: ClassVar[Int32] = 999


class NotFound(Exception, ReturnException):
    code: Int32

    def __init__(self, code: Int32) -> None:
        self.code = code


@error_return(NotFound)
def inner(key: str) -> Int32:
    if key == "x":
        return 42
    raise NotFound(7)


def outer(key: str) -> Int32:
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
