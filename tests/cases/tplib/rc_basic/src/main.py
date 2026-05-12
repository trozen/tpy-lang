# Basic Rc[T] construction, field access via get(), and method calls.
from tpy import Int32
from tplib import Rc


class State:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def doubled(self) -> Int32:
        return self.x * 2


def main() -> None:
    r = Rc.new(State(Int32(42)))
    print(r.get().x)
    print(r.get().doubled())
    r.get().x = Int32(7)
    print(r.get().x)


main()
