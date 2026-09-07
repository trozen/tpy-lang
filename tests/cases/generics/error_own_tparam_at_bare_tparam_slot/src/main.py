# An `Own[T]` param forwarded to a bare `T` method slot: the source is a move,
# not the bare forward the same-T row licenses, so the call rejects. The bare
# same-T forward is pinned by tests/cases/generics/tparam_method_arg_forward.
from tpy import Int32, Own


class Inner[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v

    def put(self, value: T) -> None:
        self.v = value


class Outer[T]:
    _in: Inner[T]

    def __init__(self, v: T) -> None:
        self._in = Inner[T](v)

    def put_own(self, value: Own[T]) -> None:
        self._in.put(value)  # tpyc: error(/method\.arg_shape/)


def main() -> None:
    o = Outer[Int32](1)
    o.put_own(9)
    print(o._in.v)


main()
