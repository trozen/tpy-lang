# Generic method called via self inside the class
from tpy import int32

class Processor[T]:
    val: T

    def __init__(self, val: T):
        self.val = val

    def wrap[U](self, x: U) -> U:
        return x

    def process(self) -> int32:
        return self.wrap(int32(99))

def main() -> None:
    p = Processor[int32](int32(1))
    print(p.process())
    print(p.wrap("hello"))

main()
