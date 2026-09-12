# Two sibling handlers reusing one capture name, each shadowing the same class:
# the throw tier registers and retires per handler, so a leak from the first into
# the second would read the wrong object, and a missed retire would leave the
# class name shadowed after the try.
from typing import ClassVar

from tpy import int32


class Registry:
    code: ClassVar[int32] = 999


class NotFound(Exception):
    def __init__(self, code: int32):
        self.code = code


class Denied(Exception):
    def __init__(self, code: int32):
        self.code = code


def pick(which: int32) -> int32:
    try:
        if which == 0:
            raise NotFound(7)
        raise Denied(20)
    except NotFound as Registry:
        return Registry.code
    except Denied as Registry:
        return Registry.code + 1
    return -1


def main() -> None:
    print(pick(0))
    print(pick(1))
    print(Registry.code)


main()
