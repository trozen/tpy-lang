# Basic Rc[T] construction, field access via get(), and method calls.
from tpy import int32
from tplib import Rc


class State:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def doubled(self) -> int32:
        return self.x * 2


def main() -> None:
    r = Rc.new(State(int32(42)))
    print(r.get().x)
    print(r.get().doubled())
    r.get().x = int32(7)
    print(r.get().x)


main()
