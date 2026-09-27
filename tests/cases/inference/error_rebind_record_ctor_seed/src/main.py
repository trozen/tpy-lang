# A generic record local rebound to a construction whose T only the local
# would pick for the argument is refused: Box([a]) holds the int a.
from tpy import int32


class Box[T]:
    def __init__(self, v: list[T]) -> None:
        self.v = v


def rebind(a: int32) -> None:
    b = Box([1.5])
    b = Box([a])  # tpyc: error(/'b' is bound to float elements at line 12 and to int32 elements here/)
    print(b.v)


rebind(3)
