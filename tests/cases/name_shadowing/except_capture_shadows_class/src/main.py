# An `except ... as NAME` capture whose name also names a module-level class:
# reads inside the handler must see the caught exception, not the class, and the
# class name must mean the class again after the handler ends.
from typing import ClassVar

from tpy import Int32


class Registry:
    code: ClassVar[Int32] = 999


class MyError(Exception):
    def __init__(self, code: Int32):
        self.code = code


def boom() -> Int32:
    try:
        raise MyError(7)
    except MyError as Registry:  # tpyc: ok
        return Registry.code
    return -1


def main() -> None:
    print(boom())
    print(Registry.code)


main()
