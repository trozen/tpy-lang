# Generic method called via self inside the class
from tpy import Int32

class Processor[T]:
    val: T

    def __init__(self, val: T):
        self.val = val

    def wrap[U](self, x: U) -> U:
        return x

    def process(self) -> Int32:
        return self.wrap(Int32(99))

def main() -> None:
    p = Processor[Int32](Int32(1))
    print(p.process())
    print(p.wrap("hello"))

main()
