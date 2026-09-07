# A generic class forwarding its method argument into a nested generic object's
# method, where both sides spell the same bare open `T`.
from tpy import Int32


class Inner[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    def put(self, value: T) -> None:
        self.v = value  # tpyc: warning(/may copy T into field/)


class Outer[T]:
    _in: Inner[T]

    def __init__(self, v: T) -> None:
        self._in = Inner[T](v)

    def put(self, value: T) -> None:
        self._in.put(value)  # tpyc: ok -- both sides spell the same open T


def main() -> None:
    o = Outer[Int32](1)
    o.put(7)
    print(o._in.v)


main()
