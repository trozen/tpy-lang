# Error: calling a T: Default bounded function with a non-Default type
from tpy import Int32, Default, make_default

class Pair:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def create_default[T: Default]() -> T:
    return make_default()

def main() -> None:
    p = create_default[Pair]()  # tpyc: error(/Default/)
    print(p.x)

main()
