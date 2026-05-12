# Rc[T].clone() shares the underlying allocation: mutation through one
# clone is observable through the other.
from tpy import Int32
from tplib import Rc


class State:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def main() -> None:
    r1 = Rc.new(State(Int32(1)))
    r2 = r1.clone()
    print(r1.get().x, r2.get().x)  # 1 1

    r1.get().x = Int32(10)
    print(r1.get().x, r2.get().x)  # 10 10 -- shared

    r2.get().x = Int32(20)
    print(r1.get().x, r2.get().x)  # 20 20 -- shared

    # Three-way share
    r3 = r2.clone()
    r3.get().x = Int32(30)
    print(r1.get().x, r2.get().x, r3.get().x)  # 30 30 30


main()
