# make_default() error: type without default constructor
from tpy import Int32, make_default

class Pair:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    p = make_default[Pair]()  # tpyc: error(/Default/)
    print(p.x)

main()
