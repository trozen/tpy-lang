# A postfix field read off a protocol method's `Own` return: that is a VALUE
# position, which the storage-return admission does not cover.
from typing import Protocol

from tpy import int32, Own


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Factory(Protocol):
    def create(self, x: int32) -> Own[Point]: ...


class DefaultFactory:
    def create(self, x: int32) -> Own[Point]:
        return Point(x)


def value_pos[T: Factory](f: T, x: int32) -> int32:
    # The Own return is read from, not stored.
    return f.create(x).x  # tpyc: error(/expr\.method_call:method\.protocol\.ret_type/)


def main() -> None:
    print(value_pos(DefaultFactory(), 3))


main()
