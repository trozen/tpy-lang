# Error: calling a T: Default bounded function with a non-Default type
from tpy import int32, Default, make_default

class Pair:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def create_default[T: Default]() -> T:
    return make_default()

def main() -> None:
    p = create_default[Pair]()  # tpyc: error(/Default/)
    print(p.x)

main()
