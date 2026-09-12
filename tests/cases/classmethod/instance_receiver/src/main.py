# A classmethod is callable through an instance, as in Python: the receiver is
# evaluated and discarded, and `cls` still binds to the defining class.
from typing import Self

from tpy import int32, Own


class Point:
    def __init__(self, x: int32):
        self.x = x

    @classmethod
    def origin(cls) -> Own[Self]:
        return cls(0)


def main() -> None:
    p = Point(7)
    q = p.origin()
    print(p.x, q.x)


main()
