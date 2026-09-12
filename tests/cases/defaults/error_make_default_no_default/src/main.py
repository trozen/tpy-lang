# make_default() error: type without default constructor
from tpy import int32, make_default

class Pair:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    p = make_default[Pair]()  # tpyc: error(/Default/)
    print(p.x)

main()
