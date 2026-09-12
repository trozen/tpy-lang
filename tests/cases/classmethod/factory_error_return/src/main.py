# @error_return on a classmethod factory: the `cls(...)` construction is the
# success value of the expected<T, E> return, and the failure propagates to the
# caller's except clause.
from typing import Self

from tpy import int32, Own, ReturnException, error_return


class Invalid(Exception, ReturnException):
    pass


class Point:
    def __init__(self, x: int32):
        self.x = x

    @error_return(Invalid)
    @classmethod
    def parse(cls, x: int32) -> Own[Self]:
        if x < 0:
            raise Invalid
        return cls(x)


def main() -> None:
    try:
        p = Point.parse(3)
        print(p.x)
        q = Point.parse(-1)
        print(q.x)
    except Invalid:
        print("invalid")


main()
