# A narrowed union subject passed into an `Own` slot: the copy temp the move
# would need can only be spelled off the extraction alias; the copy the move
# implies is warned about at the same call.
from tpy import Int32, Own


class A:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class B:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


def take_own(o: Own[A]) -> Own[A]:
    o.x += 1
    return o


def use(u: A | B) -> Int32:
    if isinstance(u, A):
        # `u` here is the narrowed read, not an ordinary lvalue.
        return take_own(u).x  # tpyc: error(/expr\.call:call\.arg_shape\.own_record_f1/)
    return 0


def main() -> None:
    print(use(A(1)))


main()
